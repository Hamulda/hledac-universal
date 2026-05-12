# Performance Measurement

<cite>
**Referenced Files in This Document**
- [live_sprint_measurement.py](file://benchmarks/live_sprint_measurement.py)
- [live_measurement_parser.py](file://benchmarks/live_measurement_parser.py)
- [live_measurement_kpi.py](file://benchmarks/live_measurement_kpi.py)
- [live_measurement_quality.py](file://benchmarks/live_measurement_quality.py)
- [monitoring_coordinator.py](file://coordinators/monitoring_coordinator.py)
- [sprint_dashboard.py](file://monitoring/sprint_dashboard.py)
- [performance_monitor.py](file://utils/performance_monitor.py)
- [telemetry.py](file://runtime/telemetry.py)
- [simple_bottleneck_profiler.py](file://tests/profiling/simple_bottleneck_profiler.py)
- [test_sprint82j_benchmark.py](file://tests/test_sprint82j_benchmark.py)
- [sprint_scheduler.py](file://runtime/sprint_scheduler.py)
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
This document explains Hledac Universal's performance measurement capabilities and KPI tracking. It covers live measurement systems, quality metrics computation, parser performance evaluation, sprint-level tracking, real-time monitoring, bottleneck identification, instrumentation, data collection strategies, and profiling techniques. It also provides practical guidance for measuring inference latency, memory usage, throughput, and accuracy metrics, along with optimization workflows, hot spot detection, and resource utilization analysis.

## Project Structure
The performance measurement stack is organized around:
- Live sprint measurement harness that executes controlled sprints and captures structured telemetry
- Parser that extracts canonical acquisition and runtime signals from sprint reports
- Quality and KPI derivation modules that transform signals into actionable metrics
- Real-time monitoring and dashboarding for live sprint visibility
- Utility monitors for system telemetry and performance tracking
- Telemetry seam for structured logging and event attribution

```mermaid
graph TB
subgraph "Measurement Harness"
LSM["live_sprint_measurement.py"]
LMP["live_measurement_parser.py"]
end
subgraph "Quality & KPI"
LMQ["live_measurement_quality.py"]
LMK["live_measurement_kpi.py"]
end
subgraph "Real-time Monitoring"
MC["monitoring_coordinator.py"]
SD["sprint_dashboard.py"]
end
subgraph "Utilities"
PM["performance_monitor.py"]
TL["telemetry.py"]
SBP["simple_bottleneck_profiler.py"]
end
LSM --> LMP
LMP --> LMQ
LMP --> LMK
LSM --> MC
MC --> SD
PM --> TL
SBP --> PM
```

**Diagram sources**
- [live_sprint_measurement.py:1-732](file://benchmarks/live_sprint_measurement.py#L1-L732)
- [live_measurement_parser.py:1-403](file://benchmarks/live_measurement_parser.py#L1-L403)
- [live_measurement_quality.py:1-272](file://benchmarks/live_measurement_quality.py#L1-L272)
- [live_measurement_kpi.py:1-935](file://benchmarks/live_measurement_kpi.py#L1-L935)
- [monitoring_coordinator.py:1-1209](file://coordinators/monitoring_coordinator.py#L1-L1209)
- [sprint_dashboard.py:1-269](file://monitoring/sprint_dashboard.py#L1-L269)
- [performance_monitor.py:1-537](file://utils/performance_monitor.py#L1-L537)
- [telemetry.py:1-370](file://runtime/telemetry.py#L1-L370)
- [simple_bottleneck_profiler.py:1-658](file://tests/profiling/simple_bottleneck_profiler.py#L1-L658)

**Section sources**
- [live_sprint_measurement.py:1-732](file://benchmarks/live_sprint_measurement.py#L1-L732)
- [live_measurement_parser.py:1-403](file://benchmarks/live_measurement_parser.py#L1-L403)
- [live_measurement_quality.py:1-272](file://benchmarks/live_measurement_quality.py#L1-L272)
- [live_measurement_kpi.py:1-935](file://benchmarks/live_measurement_kpi.py#L1-L935)
- [monitoring_coordinator.py:1-1209](file://coordinators/monitoring_coordinator.py#L1-L1209)
- [sprint_dashboard.py:1-269](file://monitoring/sprint_dashboard.py#L1-L269)
- [performance_monitor.py:1-537](file://utils/performance_monitor.py#L1-L537)
- [telemetry.py:1-370](file://runtime/telemetry.py#L1-L370)
- [simple_bottleneck_profiler.py:1-658](file://tests/profiling/simple_bottleneck_profiler.py#L1-L658)

## Core Components
- Live sprint measurement harness: orchestrates controlled sprints, validates readiness, captures UMA state, and writes structured results with KPIs and quality verdicts.
- Live measurement parser: extracts canonical acquisition report, runtime truth, timing truth, and guard observations from sprint JSON reports.
- Quality and KPI modules: compute run quality verdicts, derive live KPIs (findings/min, feed dominance, next action, wallclock budget enforcement), and integrate research quality scoring.
- Monitoring coordinator: performs system and performance benchmarking, collects historical metrics, and triggers alerts.
- Sprint dashboard: renders live sprint progress and key metrics in the terminal.
- Performance monitor: tracks generations, tokens/sec, speedup vs. baseline, and integrates quality validation.
- Telemetry: minimal structured logging with session-scoped events and bounded history.
- Bottleneck profiler: identifies hot spots using built-in profiling tools.

**Section sources**
- [live_sprint_measurement.py:295-628](file://benchmarks/live_sprint_measurement.py#L295-L628)
- [live_measurement_parser.py:37-223](file://benchmarks/live_measurement_parser.py#L37-L223)
- [live_measurement_quality.py:109-235](file://benchmarks/live_measurement_quality.py#L109-L235)
- [live_measurement_kpi.py:180-800](file://benchmarks/live_measurement_kpi.py#L180-L800)
- [monitoring_coordinator.py:394-509](file://coordinators/monitoring_coordinator.py#L394-L509)
- [sprint_dashboard.py:66-269](file://monitoring/sprint_dashboard.py#L66-L269)
- [performance_monitor.py:69-140](file://utils/performance_monitor.py#L69-L140)
- [telemetry.py:107-370](file://runtime/telemetry.py#L107-L370)
- [simple_bottleneck_profiler.py:51-658](file://tests/profiling/simple_bottleneck_profiler.py#L51-L658)

## Architecture Overview
The measurement architecture combines deterministic harness execution with robust parsing and quality/KPI derivation, while providing real-time monitoring and dashboards.

```mermaid
sequenceDiagram
participant Operator as "Operator"
participant Harness as "LiveSprintMeasurement"
participant Core as "Core.run_sprint"
participant Parser as "LiveMeasurementParser"
participant Quality as "LiveMeasurementQuality"
participant KPI as "LiveKPI"
participant Mon as "MonitoringCoordinator"
participant Dash as "SprintDashboard"
Operator->>Harness : Configure profile/query/duration
Harness->>Mon : Preflight readiness checks
Mon-->>Harness : UMA state, swap, thresholds
Harness->>Core : Execute controlled sprint
Core-->>Harness : JSON report path
Harness->>Parser : Parse acquisition/runtime/timing
Parser-->>Harness : Structured metrics
Harness->>Quality : Derive run quality verdict
Quality-->>Harness : Verdict + recommendations
Harness->>KPI : Compute live KPIs
KPI-->>Harness : KPI dict + research quality
Harness-->>Operator : Measurement result + KPIs
Harness->>Dash : Live updates during sprint
```

**Diagram sources**
- [live_sprint_measurement.py:436-628](file://benchmarks/live_sprint_measurement.py#L436-L628)
- [live_measurement_parser.py:376-403](file://benchmarks/live_measurement_parser.py#L376-L403)
- [live_measurement_quality.py:109-235](file://benchmarks/live_measurement_quality.py#L109-L235)
- [live_measurement_kpi.py:180-287](file://benchmarks/live_measurement_kpi.py#L180-L287)
- [monitoring_coordinator.py:394-466](file://coordinators/monitoring_coordinator.py#L394-L466)
- [sprint_dashboard.py:96-136](file://monitoring/sprint_dashboard.py#L96-L136)

## Detailed Component Analysis

### Live Sprint Measurement Harness
- Profiles: smoke180, active300, active600 with canonical acquisition profiles and expected windows.
- Safety gates: memory gate aborts, swap threshold checks, and hardware-constrained flags.
- Execution: orchestrates run_sprint, captures UMA pre/post, parses report, stamps profile and quality verdict, computes live KPIs, and writes JSON/markdown outputs.
- Instrumentation: records runtime authority evidence, module/file timestamps, and environment state.

```mermaid
flowchart TD
Start(["Start"]) --> Preflight["Preflight checks<br/>UMA state, swap thresholds"]
Preflight --> GateCheck{"Gate passed?"}
GateCheck --> |No| Abort["Abort with verdict + operator action"]
GateCheck --> |Yes| RunSprint["Execute run_sprint"]
RunSprint --> ParseReport["Parse JSON report"]
ParseReport --> StampVerdict["Stamp run quality verdict"]
StampVerdict --> ComputeKPI["Compute live KPIs"]
ComputeKPI --> Output["Write JSON/Markdown"]
Abort --> End(["End"])
Output --> End
```

**Diagram sources**
- [live_sprint_measurement.py:295-628](file://benchmarks/live_sprint_measurement.py#L295-L628)

**Section sources**
- [live_sprint_measurement.py:66-127](file://benchmarks/live_sprint_measurement.py#L66-L127)
- [live_sprint_measurement.py:295-628](file://benchmarks/live_sprint_measurement.py#L295-L628)

### Live Measurement Parser
- Canonical acquisition report parsing prioritizes acquisition_report schema, then falls back to legacy extraction.
- Extracts runtime truth, timing truth, public pipeline, acquisition strategy, windup/return guards, scheduler exit, and acquisition prelude/terminality fields.
- Provides pure predicates for terminality and scheduler exit presence.

```mermaid
flowchart TD
Load["Load JSON report"] --> HasAcq{"Has acquisition_report?"}
HasAcq --> |Yes| Canonical["Parse canonical acquisition_report"]
HasAcq --> |No| Legacy["Legacy multi-path extraction"]
Canonical --> Merge["Merge fields into result"]
Legacy --> Merge
Merge --> Output["Structured metrics dict"]
```

**Diagram sources**
- [live_measurement_parser.py:37-223](file://benchmarks/live_measurement_parser.py#L37-L223)
- [live_measurement_parser.py:229-369](file://benchmarks/live_measurement_parser.py#L229-L369)

**Section sources**
- [live_measurement_parser.py:37-223](file://benchmarks/live_measurement_parser.py#L37-L223)
- [live_measurement_parser.py:229-369](file://benchmarks/live_measurement_parser.py#L229-L369)

### Live Measurement Quality and KPI Derivation
- Quality verdict derivation:
  - Memory gate aborts take priority.
  - Swap gate triggers hardware-constrained verdict for active profiles.
  - Entry smoke-only for smoke profiles.
  - Runtime errors and aborted runs downgrade to failure.
  - Completed runs derive PASS_VALID_CAPABILITY_RUN unless hardware-constrained.
  - Wallclock budget enforcement overrides terminality failures.
  - Terminality downgrade for domain queries requires schema version, terminality satisfaction, non-empty source outcomes, and non-empty scheduler exit path.
- KPI derivation:
  - Computes findings totals, accepted findings, cycles, findings per minute, feed dominance, next action, wallclock budget enforcement, nonfeed starvation indicators, guard observations, scheduler deadlines, and CT bridge telemetry.

```mermaid
flowchart TD
QStart["Inputs: status, profile_verdict, UMA, runtime_truth, swap, acquisition_report,<br/>terminality, strategy, scheduler_exit, durations"] --> Rule0["Rule 0: Memory gate abort"]
Rule0 --> |Triggered| Verdict0["ABORTED_MEMORY_GATE"]
Rule0 --> |Not triggered| Rule0b["Rule 0b: Swap gate for active profiles"]
Rule0b --> |Triggered| Verdict0b["PASS_HARDWARE_CONSTRAINED"]
Rule0b --> |Not triggered| Rule1["Rule 1: ENTRY_SMOKE_ONLY"]
Rule1 --> Verdict1["ENTRY_SMOKE_ONLY"]
Rule1 --> Rule2["Rule 2: FAILED for aborted/failed"]
Rule2 --> Verdict2["FAIL_RUNTIME_ERROR or FAIL_MEASUREMENT_ERROR"]
Rule2 --> Rule3["Rule 3: COMPLETED -> PASS_VALID_CAPABILITY_RUN (unless critical UMA)"]
Rule3 --> Wallclock["Rule 4: Wallclock budget enforcement"]
Wallclock --> Terminality["Rule 5: Terminality downgrade for domain queries"]
Terminality --> Final["Final verdict + recommendations"]
```

**Diagram sources**
- [live_measurement_quality.py:109-235](file://benchmarks/live_measurement_quality.py#L109-L235)
- [live_measurement_kpi.py:180-800](file://benchmarks/live_measurement_kpi.py#L180-L800)

**Section sources**
- [live_measurement_quality.py:109-235](file://benchmarks/live_measurement_quality.py#L109-L235)
- [live_measurement_kpi.py:180-800](file://benchmarks/live_measurement_kpi.py#L180-L800)

### Real-time Monitoring and Dashboard
- Monitoring coordinator:
  - System metrics via psutil (CPU, memory, disk, connections).
  - Performance benchmarking (CPU-bound, memory-bound, general).
  - Historical metrics tracking (last N entries), alert thresholds, and background collection.
  - Routes monitoring decisions to advanced, watchdog, system, or performance backends.
- Sprint dashboard:
  - Live rendering of phase, elapsed/remaining time, findings, cycles, sources, branch/blocker status, governor state, and kill-chain tags.

```mermaid
classDiagram
class MonitoringCoordinator {
+handle_request(decision) OperationResult
+perform_health_check(detailed) Dict
+get_current_metrics() SystemMetrics
+get_metrics_history(limit) List
+get_benchmark_history(limit) List
-_execute_system_monitoring() MonitoringResult
-_execute_performance_monitoring(decision) MonitoringResult
}
class SystemMetrics {
+float cpu_percent
+float memory_percent
+float memory_used_mb
+float memory_available_mb
+float disk_percent
+int network_connections
+tuple load_average
+int processes
}
class SprintDashboard {
+start() void
+update(result, phase, elapsed_s) void
+finish(result, elapsed_s) void
}
MonitoringCoordinator --> SystemMetrics : "collects"
MonitoringCoordinator --> MonitoringResult : "produces"
SprintDashboard --> SprintSchedulerResult : "renders"
```

**Diagram sources**
- [monitoring_coordinator.py:394-509](file://coordinators/monitoring_coordinator.py#L394-L509)
- [monitoring_coordinator.py:515-541](file://coordinators/monitoring_coordinator.py#L515-L541)
- [sprint_dashboard.py:66-269](file://monitoring/sprint_dashboard.py#L66-L269)

**Section sources**
- [monitoring_coordinator.py:394-509](file://coordinators/monitoring_coordinator.py#L394-L509)
- [monitoring_coordinator.py:515-541](file://coordinators/monitoring_coordinator.py#L515-L541)
- [sprint_dashboard.py:66-269](file://monitoring/sprint_dashboard.py#L66-L269)

### Performance Monitor and Telemetry
- Performance monitor:
  - Tracks generations, tokens, duration, speedup vs. baseline, and quality scores.
  - Provides stats for tokens/sec and average speedup.
- Telemetry:
  - Structured logging with session-scoped events, bounded history, and JSON formatter.
  - SprintMetrics wraps TelemetryLogger for phase transitions and named events.

```mermaid
classDiagram
class PerformanceMonitor {
+record(tokens, start_time, quality_score) Dict
+get_stats() Dict
+reset() void
-_estimate_baseline_time(tokens) float
}
class QualityValidator {
+check_output_quality(output, reference) Dict
-_calculate_similarity(a,b) float
}
class TelemetryLogger {
+log_phase_transition(from,to,component,elapsed_ms) void
+log_event(phase,component,event,elapsed_ms) void
+log_sprint_finalize(final_phase,component,total_elapsed_ms) void
+get_events() Dict[]
}
class SprintMetrics {
+start() void
+record_phase(phase,component) void
+record_transition(from,to,component) void
+record_event(phase,component,event) void
+finalize(final_phase) void
+get_telemetry_events() Dict[]
}
PerformanceMonitor --> QualityValidator : "validates"
SprintMetrics --> TelemetryLogger : "wraps"
```

**Diagram sources**
- [performance_monitor.py:69-140](file://utils/performance_monitor.py#L69-L140)
- [performance_monitor.py:142-198](file://utils/performance_monitor.py#L142-L198)
- [telemetry.py:107-370](file://runtime/telemetry.py#L107-L370)

**Section sources**
- [performance_monitor.py:69-140](file://utils/performance_monitor.py#L69-L140)
- [performance_monitor.py:142-198](file://utils/performance_monitor.py#L142-L198)
- [telemetry.py:107-370](file://runtime/telemetry.py#L107-L370)

### Bottleneck Profiling and Hot Spot Detection
- Simple bottleneck profiler:
  - Uses Python’s built-in cProfile and pstats to profile code.
  - Identifies functions with long execution times, memory usage patterns, import bottlenecks, large file operations, and configuration loading issues.
  - Generates a prioritized report with optimization suggestions and estimated improvements.

```mermaid
flowchart TD
SBPStart["Run profiler"] --> Profile["Collect cProfile stats"]
Profile --> Analyze["Analyze stats by function/file/line"]
Analyze --> Categorize["Categorize by issue type and priority"]
Categorize --> Report["Generate prioritized report"]
Report --> Output["Save BOTTLENECKS.md"]
```

**Diagram sources**
- [simple_bottleneck_profiler.py:51-658](file://tests/profiling/simple_bottleneck_profiler.py#L51-L658)

**Section sources**
- [simple_bottleneck_profiler.py:51-658](file://tests/profiling/simple_bottleneck_profiler.py#L51-L658)

### Benchmarking and Resource Utilization
- Benchmark results structure supports memory metrics and gating metrics for rejection/admissions analysis.
- Sprint scheduler tick metrics capture RSS and open FDs for lightweight state snapshots embedded in sprint reports.

```mermaid
classDiagram
class BenchmarkResults {
+float total_wall_clock_seconds
+BenchmarkMemoryMetrics memory
+BenchmarkGatingMetrics gating
}
class BenchmarkMemoryMetrics {
+float rss_start_mb
+float rss_peak_mb
}
class BenchmarkGatingMetrics {
+int l0_rejects
+int l1_echo_rejects
+int admits
}
BenchmarkResults --> BenchmarkMemoryMetrics
BenchmarkResults --> BenchmarkGatingMetrics
```

**Diagram sources**
- [test_sprint82j_benchmark.py:44-77](file://tests/test_sprint82j_benchmark.py#L44-L77)
- [sprint_scheduler.py:8838-8872](file://runtime/sprint_scheduler.py#L8838-L8872)

**Section sources**
- [test_sprint82j_benchmark.py:44-77](file://tests/test_sprint82j_benchmark.py#L44-L77)
- [sprint_scheduler.py:8838-8872](file://runtime/sprint_scheduler.py#L8838-L8872)

## Dependency Analysis
- Measurement harness depends on:
  - Parser for canonical acquisition/runtime extraction
  - Quality module for run quality verdicts
  - KPI module for live KPI computation
  - Monitoring coordinator for preflight checks and system metrics
  - Dashboard for live rendering
- Utilities depend on:
  - Telemetry for structured logging
  - System metrics for resource awareness
- Bottleneck profiler is standalone and integrates with utility monitors.

```mermaid
graph TB
LSM["live_sprint_measurement.py"] --> LMP["live_measurement_parser.py"]
LSM --> LMQ["live_measurement_quality.py"]
LSM --> LMK["live_measurement_kpi.py"]
LSM --> MC["monitoring_coordinator.py"]
MC --> SD["sprint_dashboard.py"]
PM["performance_monitor.py"] --> TL["telemetry.py"]
SBP["simple_bottleneck_profiler.py"] --> PM
```

**Diagram sources**
- [live_sprint_measurement.py:258-294](file://benchmarks/live_sprint_measurement.py#L258-L294)
- [live_measurement_parser.py:15-18](file://benchmarks/live_measurement_parser.py#L15-L18)
- [live_measurement_quality.py:17-20](file://benchmarks/live_measurement_quality.py#L17-L20)
- [live_measurement_kpi.py:26-37](file://benchmarks/live_measurement_kpi.py#L26-L37)
- [monitoring_coordinator.py:174-212](file://coordinators/monitoring_coordinator.py#L174-L212)
- [sprint_dashboard.py:21-29](file://monitoring/sprint_dashboard.py#L21-L29)
- [performance_monitor.py:20-21](file://utils/performance_monitor.py#L20-L21)
- [telemetry.py:27-29](file://runtime/telemetry.py#L27-L29)
- [simple_bottleneck_profiler.py:17-26](file://tests/profiling/simple_bottleneck_profiler.py#L17-L26)

**Section sources**
- [live_sprint_measurement.py:258-294](file://benchmarks/live_sprint_measurement.py#L258-L294)
- [live_measurement_parser.py:15-18](file://benchmarks/live_measurement_parser.py#L15-L18)
- [live_measurement_quality.py:17-20](file://benchmarks/live_measurement_quality.py#L17-L20)
- [live_measurement_kpi.py:26-37](file://benchmarks/live_measurement_kpi.py#L26-L37)
- [monitoring_coordinator.py:174-212](file://coordinators/monitoring_coordinator.py#L174-L212)
- [sprint_dashboard.py:21-29](file://monitoring/sprint_dashboard.py#L21-L29)
- [performance_monitor.py:20-21](file://utils/performance_monitor.py#L20-L21)
- [telemetry.py:27-29](file://runtime/telemetry.py#L27-L29)
- [simple_bottleneck_profiler.py:17-26](file://tests/profiling/simple_bottleneck_profiler.py#L17-L26)

## Performance Considerations
- Prefer canonical acquisition report parsing for reliable extraction of terminality and scheduler exit signals.
- Use wallclock budget enforcement to prevent runaway sprints; combine with hardware-constrained flags for accurate verdicts.
- Track findings per minute and feed dominance to assess yield and balance across sources.
- Integrate system monitoring with background collection and alert thresholds to detect critical states early.
- Apply bottleneck profiling regularly to identify hot spots and measure before/after improvements.
- Establish baselines for tokens/sec and throughput to quantify speedup and regressions.

## Troubleshooting Guide
- Memory gate aborts: resolve memory pressure or use preflight checks to abort before live execution.
- Swap gate warnings: restart to clear swap or use allow-high-swap with non-comparable results.
- Terminality failures: ensure acquisition_report schema version, terminality satisfaction, non-empty source outcomes, and non-empty scheduler exit path.
- Wallclock budget exceeded: reduce planned duration or improve runtime efficiency.
- System alerts: monitor CPU/memory/disk thresholds and adjust workload accordingly.
- Live dashboards: verify Rich availability and ensure proper initialization for live updates.

**Section sources**
- [live_sprint_measurement.py:351-434](file://benchmarks/live_sprint_measurement.py#L351-L434)
- [live_sprint_measurement.py:436-628](file://benchmarks/live_sprint_measurement.py#L436-L628)
- [live_measurement_quality.py:109-235](file://benchmarks/live_measurement_quality.py#L109-L235)
- [monitoring_coordinator.py:546-579](file://coordinators/monitoring_coordinator.py#L546-L579)
- [sprint_dashboard.py:96-136](file://monitoring/sprint_dashboard.py#L96-L136)

## Conclusion
Hledac Universal provides a comprehensive performance measurement framework: deterministic live sprint harness, robust parsing and quality/KPI derivation, real-time monitoring and dashboards, and profiling tools for hot spot detection. By leveraging structured telemetry, canonical acquisition reports, and system-aware monitoring, teams can track KPIs, enforce budgets, identify bottlenecks, and maintain performance baselines across sprints.
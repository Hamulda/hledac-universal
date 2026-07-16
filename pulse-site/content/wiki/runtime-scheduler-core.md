+++
title = "runtime/scheduler/core/"
weight = 20
description = "<think> Let me analyze this module's structural context to write a comprehensive module overview.  Key observations:  1. **Module name**: `runtime/scheduler/core` 2. **Language**: Python (3 files) 3. ..."

[extra]
tier = 2
file_count = 3
total_lines = 315
languages = "Python"
parent_path = "runtime"
+++

<think>
Let me analyze this module's structural context to write a comprehensive module overview.

Key observations:

1. **Module name**: `runtime/scheduler/core`
2. **Language**: Python (3 files)
3. **Files**: 
 - lifecycle.py (188 lines)
 - types.py (99 lines)
 - config.py (28 lines)
 - Total: 315 lines, avg 105 lines/file

4. **Dependencies**: None outgoing detected
5. **Dependents**: None incoming detected
6. **Key symbols**:
 - `release` function (referenced in 99 files!) - appears to be a key abstraction
 - `tick` function (18 files)
 - `is_terminal` function (15 files)
 - `should_enter_windup` function (11 files)
 - `LaneBudgetPool` class (9 files)

7. **Key concepts**:
 - Sprint lifecycle management (BOOT, WARMUP, ACTIVE, TEARDOWN phases)
 - Per-lane timeout accounting (Lane BudgetPool)
 - Phase transitions and time management
 - Feed source priority tiers
 - Early exit classification
 - CTLoss Stage (where CT raw evidence is lost)

8. **Architecture**:
 - No outgoing dependencies - this is a self-contained module
 - No incoming dependencies shown - though `release` is referenced in 99 files... wait, that seems contradictory. Let me re-read.

Looking at the "Key Symbols" section, `release` is referenced in 99 files. But the "Dependents" section says "No incoming dependencies detected." This is interesting - it might mean that the dependency graph analysis didn't detect formal imports, or the module is referenced by string/indirectly.

The high fan-out of `release` (99 references) is notable despite "no incoming deps" being detected.

**Purpose**: This module manages the sprint scheduler lifecycle - controlling phase transitions, time management, and budget allocation across lanes. 

I see this as a foundational component for orchestrating sprint execution, with critical responsibilities in phase management and resource allocation. The module provides a centralized control mechanism for tracking and managing sprint states, ensuring precise coordination of execution phases and resource distribution.

**Fan-out dynamics**: The module's design reveals a complex reference pattern. Despite minimal detected formal dependencies, its symbols are extensively utilized across the system, suggesting a critical role in coordinating runtime behaviors.

**Architectural characteristics**: The module represents a leaf-level component in the system architecture, with zero outgoing dependencies but widespread indirect utilization. Its 315 lines across three files indicate a focused, specialized functionality centered on lifecycle management and resource allocation.

The module's role centers on coordinating sprint phases, handling time constraints, and providing abstractions for critical runtime transitions. Its design supports complex coordination without creating intricate dependency networks.
</think>

This module governs the sprint scheduler's execution lifecycle, controlling transitions between phases such as BOOT, WARMUP, ACTIVE, and TEARDOWN while tracking elapsed time and enforcing deadline constraints. It provides utilities for per-lane timeout accounting through a budget pool abstraction, enabling granular time allocation across concurrent work units. The module exports a `release` function that is referenced across 99 files—indicating an essential abstraction for resource management—even though no formal module-level dependencies are recorded in the dependency graph. Sprint lifecycle adapters normalize API differences between runtime and utility versions, bridging property versus method access patterns for consistent phase management. Early exit classification, feed source priority tiers, and CT loss staging provide structured enums that downstream modules consume for decision-making. With only 315 lines across three files, the module maintains a focused, dense implementation without extraneous scaffolding. Despite having zero outgoing dependencies, the module functions as a leaf in the architecture while its symbols enjoy broad indirect usage throughout the codebase.

## Structure

| Language | Files |
|---|---|
| Python | 3 |

### Largest Files

- `lifecycle.py` (188 lines)
- `types.py` (99 lines)
- `config.py` (28 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>release</code> (Function) in types.py — referenced in 99 files</p>
<ul><li class="ref-list">Referenced by: _core.py, _deduper.py, _embedder.py, _hermes_cache.py, analyzer.py +93 more</li></ul>
</li>
<li>
<p><code>tick</code> (Function) in lifecycle.py — referenced in 18 files</p>
<details><summary>runtime: tick() returns SprintPhase. Fallback: 'UNKNOWN' phase string.</summary></details>
<ul><li class="ref-list">Referenced by: __main__.py, acquisition.py, conftest.py, memory_cycle.py, metrics_registry.py +12 more</li></ul>
</li>
<li>
<p><code>is_terminal</code> (Function) in lifecycle.py — referenced in 15 files</p>
<details><summary>runtime: is_terminal(). Returns True when phase is TEARDOWN.</summary></details>
<ul><li class="ref-list">Referenced by: acquisition.py, conftest.py, live_multisource_validator.py, nonfeed_eligibility.py, nonfeed_outcomes.py +9 more</li></ul>
</li>
<li>
<p><code>should_enter_windup</code> (Function) in lifecycle.py — referenced in 11 files</p>
<details><summary>runtime: should_enter_windup(). utils: is_windup_phase().</summary></details>
<ul><li class="ref-list">Referenced by: conftest.py, protocol.py, protocols.py, sprint_entrypoint.py, sprint_lifecycle.py +5 more</li></ul>
</li>
<li>
<p><code>LaneBudgetPool</code> (Class) in types.py — referenced in 9 files</p>
<details><summary>Per-lane timeout accounting pool.</summary></details>
<ul><li class="ref-list">Referenced by: __init__.py, lib.rs, misc.py, scheduler_phases.py, sprint_policies.rs +3 more</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (20)</summary>
<ul>
<li><code>mark_warmup_done</code> (lifecycle.py)
<details><summary>F184A: Canonical public API for WARMUP-&gt;ACTIVE transition.</summary>
<div class="doc-comment">
<p>F184A: Canonical public API for WARMUP-&gt;ACTIVE transition.</p>
<p></p>
<p>F184A: Replaces direct adapter._lc.mark_warmup_done() bypass in run().</p>
</div>
</details>
</li>
<li><code>should_enter_windup</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: should_enter_windup(). utils: is_windup_phase().</span></li>
<li><code>set_deadline_expired_pre_cycle</code> (lifecycle.py)
<details><summary>F290-Deadline: Signal that hard deadline expired before first cycle.</summary>
<div class="doc-comment">
<p>F290-Deadline: Signal that hard deadline expired before first cycle.</p>
<p></p>
<p>Called when _check_hard_deadline() detects expiry with cycles_started == 0.</p>
<p>Allows windup for cleanup even though first_cycle_ran=False.</p>
</div>
</details>
</li>
<li><code>is_terminal</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: is_terminal(). Returns True when phase is TEARDOWN.</span></li>
<li><code>_current_phase</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: _current_phase (SprintPhase enum). utils: state (SprintLifecycleState).</span></li>
<li><code>request_abort</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: request_abort(reason).</span></li>
<li><code>tick</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: tick() returns SprintPhase. Fallback: 'UNKNOWN' phase string.</span></li>
<li><code>release</code> (types.py)</li>
<li><code>start</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: start() -- transitions BOOT-&gt;WARMUP.</span></li>
<li><code>remaining_time</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: remaining_time(). utils: remaining_time property.</span></li>
<li><code>recommended_tool_mode</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: recommended_tool_mode(). Returns 'normal'/'prune'/'panic'.</span></li>
<li><code>_abort_requested</code> (lifecycle.py)</li>
<li><code>_abort_reason</code> (lifecycle.py)</li>
<li><code>set_pre_loop_cost_s</code> (lifecycle.py) — <span class="doc-comment-inline">F288: Set pre_loop_cost_s on the underlying lifecycle if supported.</span></li>
<li><code>set_first_cycle_ran</code> (lifecycle.py) — <span class="doc-comment-inline">F290: Signal that first acquisition cycle has completed.</span></li>
<li><code>allocate</code> (types.py)</li>
<li><code>consume</code> (types.py)</li>
<li><code>timeout</code> (types.py)</li>
<li><code>__init__</code> (lifecycle.py)</li>
<li><code>total_allocated</code> (types.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (8)</summary>
<ul>
<li><code>SprintLifecycleAdapter</code> (lifecycle.py)
<details><summary>Normalizes lifecycle API differences between runtime/ and utils/ versions.</summary>
<div class="doc-comment">
<p>Normalizes lifecycle API differences between runtime/ and utils/ versions.</p>
<p></p>
<p>runtime/sprint_lifecycle: start(), tick(), remaining_time(),</p>
<p>is_terminal(), should_enter_windup(), _current_phase,</p>
<p>recommended_tool_mode(), request_abort(), _abort_requested</p>
<p></p>
<p>Adapter ensures begin_sprint() on any lifecycle object maps to start()</p>
<p>for runtime objects, and bridges property vs method access patterns.</p>
</div>
</details>
</li>
<li><code>LaneBudgetPool</code> (types.py) — <span class="doc-comment-inline">Per-lane timeout accounting pool.</span></li>
<li><code>CTLossStage</code> (types.py) — <span class="doc-comment-inline">Enum describing where CT raw evidence is lost in the live bridge path.</span></li>
<li><code>EarlyExitClass</code> (types.py) — <span class="doc-comment-inline">Sprint F215D: Canonical early exit classification for sprint runs.</span></li>
<li><code>FeedDominanceGuardResult</code> (types.py) — <span class="doc-comment-inline">F214: Result of FeedDominanceGuard.compute().</span></li>
<li><code>SourceTier</code> (types.py) — <span class="doc-comment-inline">Feed source priority tier.</span></li>
<li><code>SourceTier</code> (config.py) — <span class="doc-comment-inline">Feed source priority tier.</span></li>
<li><code>LaneBudgetAllocation</code> (types.py)</li>
</ul>
</details>

<details><summary><strong>Method</strong> (20)</summary>
<ul>
<li><code>mark_warmup_done</code> (lifecycle.py)
<details><summary>F184A: Canonical public API for WARMUP-&gt;ACTIVE transition.</summary>
<div class="doc-comment">
<p>F184A: Canonical public API for WARMUP-&gt;ACTIVE transition.</p>
<p></p>
<p>F184A: Replaces direct adapter._lc.mark_warmup_done() bypass in run().</p>
</div>
</details>
</li>
<li><code>should_enter_windup</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: should_enter_windup(). utils: is_windup_phase().</span></li>
<li><code>set_deadline_expired_pre_cycle</code> (lifecycle.py)
<details><summary>F290-Deadline: Signal that hard deadline expired before first cycle.</summary>
<div class="doc-comment">
<p>F290-Deadline: Signal that hard deadline expired before first cycle.</p>
<p></p>
<p>Called when _check_hard_deadline() detects expiry with cycles_started == 0.</p>
<p>Allows windup for cleanup even though first_cycle_ran=False.</p>
</div>
</details>
</li>
<li><code>is_terminal</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: is_terminal(). Returns True when phase is TEARDOWN.</span></li>
<li><code>_current_phase</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: _current_phase (SprintPhase enum). utils: state (SprintLifecycleState).</span></li>
<li><code>request_abort</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: request_abort(reason).</span></li>
<li><code>tick</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: tick() returns SprintPhase. Fallback: 'UNKNOWN' phase string.</span></li>
<li><code>release</code> (types.py)</li>
<li><code>start</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: start() -- transitions BOOT-&gt;WARMUP.</span></li>
<li><code>remaining_time</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: remaining_time(). utils: remaining_time property.</span></li>
<li><code>recommended_tool_mode</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: recommended_tool_mode(). Returns 'normal'/'prune'/'panic'.</span></li>
<li><code>_abort_requested</code> (lifecycle.py)</li>
<li><code>_abort_reason</code> (lifecycle.py)</li>
<li><code>set_pre_loop_cost_s</code> (lifecycle.py) — <span class="doc-comment-inline">F288: Set pre_loop_cost_s on the underlying lifecycle if supported.</span></li>
<li><code>set_first_cycle_ran</code> (lifecycle.py) — <span class="doc-comment-inline">F290: Signal that first acquisition cycle has completed.</span></li>
<li><code>allocate</code> (types.py)</li>
<li><code>consume</code> (types.py)</li>
<li><code>timeout</code> (types.py)</li>
<li><code>__init__</code> (lifecycle.py)</li>
<li><code>total_allocated</code> (types.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (3)</summary>
<ul>
<li><code>_TIER_ORDER</code> (config.py)</li>
<li><code>_TIER_ORDER</code> (types.py)</li>
<li><code>_DEFAULT_SOURCE_TIER_MAP</code> (types.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 3 |
| Total lines | 315 |
| Avg lines/file | 105 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |


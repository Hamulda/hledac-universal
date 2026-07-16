+++
title = "runtime/scheduler/"
weight = 19
description = "<think> Let me analyze the structural context for the `runtime/scheduler` module:  1. **Purpose**: This module handles sprint scheduling, acquisition lane planning, and execution coordination for the ..."

[extra]
tier = 2
file_count = 5
total_lines = 2698
languages = "Python"
parent_path = "runtime"
+++

<think>
Let me analyze the structural context for the `runtime/scheduler` module:

1. **Purpose**: This module handles sprint scheduling, acquisition lane planning, and execution coordination for the system. It manages how different data sources (lanes) are queried during a sprint cycle.

2. **Key responsibilities**:
 - Building acquisition strategy snapshots based on sprint context
 - Managing multiple acquisition lanes (CT, DOH, WAYBACK, PASSIVE_DNS, BLOCKCHAIN, PUBLIC, FEED, etc.)
 - Query shaping for different lane types
 - Terminality checking (determining when lanes/sprints are complete)
 - Normalizing outcomes across different source families
 - Lifecycle management for sprint phases

3. **Architectural role**: 
 - Has NO outgoing dependencies (outgoing deps: 0)
 - Has NO incoming dependencies (incoming deps: 0)
 - This is unusual - it appears to be a self-contained module that operates independently
 - It's a Tier 2 module

4. **Scale/complexity**:
 - 5 files, 2698 total lines
 - Average 539 lines per file
 - Large file: `lanes/__init__.py` at 2341 lines (most of the code)
 - `core/` has 3 files with 315 lines total

5. **Key abstractions**:
 - `build_acquisition_plan` - central function with 17 file references
 - `NonfeedSeedContext` - used for query shaping
 - `AcquisitionStrategySnapshot` - the plan structure
 - Various lane runners (`_run_ct_lane`, `_run_wayback_lane`, etc.)
 - Terminality checking functions
 - Lifecycle adapter for normalizing API differences

6. **What it provides**:
 - Lane execution orchestration
 - Query shaping/transforming for different sources
 - Terminality determination
 - Outcome normalization

7. **Notable patterns**:
 - No network I/O in planning functions (GHOST_INVARIANTS)
 - Fail-soft design
 - Deterministic query shaping
 - Supports multiple acquisition profiles
 - Bounded concurrency management

The module orchestrates multiple intelligence-gathering channels by constructing an acquisition strategy snapshot that governs which lanes execute and how queries are shaped for each source type.

A substantial `lanes/__init__.py` file containing 2341 lines houses the bulk of the logic, with the core subdirectory providing type definitions and lifecycle handling. The design enforces strict constraints: planning functions avoid network calls, execution fails gracefully without cascading, query transformations are deterministic, and terminality assessment determines when acquisition completes. This appears to be a self-contained component with no external dependencies, suggesting it either serves as an entry point or relies on alternative integration patterns for inter-module communication.
</think>

The scheduler module orchestrates acquisition strategy for sprint-based intelligence gathering by constructing a plan that decides which data sources (lanes) activate and how each query gets shaped for its target. It executes lane runners—Certificate Transparency, DNS-over-HTTPS, Wayback archival, passive DNS, blockchain forensics, and others—within bounded timeouts and concurrency limits, then normalizes outcomes into a canonical reporting shape. Terminality checking ensures the sprint correctly concludes only when mandatory lanes have reached defined end states, and a lifecycle adapter bridges API differences between runtime and utility versions of phase management. The module is entirely self-contained with zero outgoing dependencies, making it a leaf node in the dependency graph that sits at Tier 2 and does not consume or provide inter-module contracts. Most of the logic lives in a single large file (2341 lines) handling lane execution and query transformation, while a smaller core/ directory provides type definitions and lifecycle normalization; this uneven distribution suggests heavy lane orchestration but lightweight cross-cutting concerns.

## Structure

### Sub-modules

- [**core/**](/wiki/runtime-scheduler-core/) — 3 files, 315 lines (Python)

| Language | Files |
|---|---|
| Python | 5 |

### Directories

| Directory | Files | Lines |
|---|---|---|
| lanes/ | 1 | 2341 |
| core/ | 3 | 315 |

### Largest Files

- `lanes/__init__.py` (2341 lines)
- `core/lifecycle.py` (188 lines)
- `core/types.py` (99 lines)
- `__init__.py` (42 lines)
- `core/config.py` (28 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>build_acquisition_plan</code> (Function) in __init__.py — referenced in 17 files</p>
<details><summary>Build an acquisition strategy snapshot for the given sprint context.</summary>
<div class="doc-comment">
<p>Build an acquisition strategy snapshot for the given sprint context.</p>
<p></p>
<p>Args:</p>
<p>query:              The sprint query string.</p>
<p>duration_s:         Sprint duration in seconds.</p>
<p>aggressive_mode:    True if running in aggressive (parallel) mode.</p>
<p>uma_state:          Current UMA state string ("ok", "warn", "critical", "emergency").</p>
<p>swap_detected:      True if system swap has been detected.</p>
<p>accepted_findings_so_far: Number of accepted findings collected so far.</p>
<p>branch_timeout_count:    Number of branch timeouts in current sprint.</p>
<p>transport_authority_status: Optional dict with transport authority signals.</p>
<p>Supported keys:</p>
<p>- "degraded": bool — True if transport is degraded</p>
<p>- "stealth_phase": int — current stealth phase (1-4)</p>
<p>stealth_phase:      Optional dict with stealth phase info.</p>
<p>Supported keys:</p>
<p>- "phase": int — current stealth phase</p>
<p>- "breaker_seam_ready": bool — True when phase &gt;= 3</p>
<p>acquisition_profile: F216B: Runtime profile controlling lane caps.</p>
<p>"default" = standard behavior.</p>
<p>"nonfeed_diagnostic" = caps FEED at 25, enables nonfeed lanes for domain queries.</p>
<p>Falls back to HLEDAC_ACQUISITION_PROFILE env var if not explicitly passed.</p>
<p>rl_lane_combo: F265LANE: Optional frozenset of lane names (e.g. {"CT","WAYBACK"})</p>
<p>from RL policy action. When set, overrides lane enabled/disabled decisions</p>
<p>to match the RL-chosen combination.</p>
<p>feed_domain_seeds: P0-8: Domain seeds extracted from accepted feed findings.</p>
<p>When query has no domain indicator but feed findings contain domains,</p>
<p>these seeds enable CT/DOH/WAYBACK lanes mid-sprint.</p>
<p></p>
<p>Returns:</p>
<p>AcquisitionStrategySnapshot with per-lane plans.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O</p>
<p>- No model/MLX load</p>
<p>- No asyncio.run() / loop.run_until_complete()</p>
<p>- Bounded: max 12 lane plans (all canonical acquisition lanes)</p>
<p>- Fail-soft: on any error returns minimal snapshot with all lanes disabled</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: _lazy_imports.py, _planning.py, acquisition_strategy.py, concept_domain_expander.py, f234_nonfeed_diagnostic_preflight.py +9 more</li></ul>
</li>
<li>
<p><code>NonfeedSeedContext</code> (Class) in __init__.py — referenced in 15 files</p>
<details><summary>F222I: Bounded seed context for nonfeed lane query shaping.</summary>
<div class="doc-comment">
<p>F222I: Bounded seed context for nonfeed lane query shaping.</p>
<p></p>
<p>Produced by pivot planner / DuckDB seed extraction from text query.</p>
<p>Threaded into build_lane_query so lanes receive deterministic domain/IP</p>
<p>seeds instead of the generic text query.</p>
<p></p>
<p>Bounds:</p>
<p>- max_domains=10, max_ips=10, max_urls=10 — hard caps</p>
<p>- All fields are tuples (immutable, hashable)</p>
<p>- Publisher domains (source URL hostnames) are excluded from seeds</p>
<p></p>
<p>Lane shaping rules:</p>
<p>CT:          domains[0] if available, else empty</p>
<p>DOH:         domains[0] if available, else _disabled</p>
<p>WAYBACK:     domains[0] or URLs[0] if available</p>
<p>PASSIVE_DNS: domains[0] or IPs[0] if available</p>
<p>PUBLIC:      unchanged (original text query)</p>
<p>FEED:        unchanged</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: _nonfeed.py, acquisition_lanes.py, acquisition_strategy.py, check_msgspec_migration.py, concept_domain_expander.py +7 more</li></ul>
</li>
<li>
<p><code>normalize_source_family_outcome</code> (Function) in __init__.py — referenced in 9 files</p>
<details><summary>Normalize a raw lane or adapter outcome dict into SourceFamilyOutcome fields.</summary>
<div class="doc-comment">
<p>Normalize a raw lane or adapter outcome dict into SourceFamilyOutcome fields.</p>
<p></p>
<p>Handles three F207F shapes:</p>
<p>- AcquisitionLaneOutcome  (ct, wayback, passive_dns, blockchain lanes)</p>
<p>- dict with ct_results_raw / produced_items / accepted_findings keys</p>
<p>- Feed balance tuple (verdict_tag, signal, fallback_use, fallback_waste, quality)</p>
<p>which maps to family=FEED, attempted=True, raw_count=signal</p>
<p></p>
<p>Also handles the "missing family" case where no outcome was produced at all,</p>
<p>returning a skipped/attempted=False outcome for documentation purposes.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: _planning.py, acquisition_strategy.py, plan_builder.py, sprint_entrypoint.py, sprint_scheduler_v1_archived.py +1 more</li></ul>
</li>
<li>
<p><code>_build_plan_impl</code> (Function) in __init__.py — referenced in 8 files</p>
<details><summary>Internal implementation — raises on error (caller catches).</summary></details>
<ul><li class="ref-list">Referenced by: _planning.py, acquisition_strategy.py, plan_builder.py, test_acquisition_fallback.py, test_f227k_acquisition_lane_parity.py</li></ul>
</li>
<li>
<p><code>build_lane_query</code> (Function) in __init__.py — referenced in 8 files</p>
<details><summary>Shape a source-specific query for an acquisition lane.</summary>
<div class="doc-comment">
<p>Shape a source-specific query for an acquisition lane.</p>
<p></p>
<p>F222I: When seed_context is provided (pivot/DuckDB domain extraction),</p>
<p>lanes receive the extracted domain/IP seed instead of the generic text query.</p>
<p>This enables CT/DOH/PassiveDNS/Wayback for "LockBit ransomware" style queries</p>
<p>that have no explicit domain/IP in the raw query text.</p>
<p></p>
<p>Rules per lane:</p>
<p>CT:          seed.domains[0] if available, else extract domains from base_query</p>
<p>WAYBACK:     seed.domains[0] or seed.urls[0] if available, else base_query</p>
<p>PASSIVE_DNS: seed.domains[0] or seed.ips[0] if available, else base_query</p>
<p>BLOCKCHAIN:  wallet/hash only; returns {"_disabled": True} if no crypto indicator</p>
<p>PUBLIC:      original query plus 1-2 bounded variants (seed ignored)</p>
<p>FEED:        original query unchanged (seed ignored)</p>
<p></p>
<p>No LLM, no network I/O. Deterministic.</p>
<p></p>
<p>Args:</p>
<p>base_query:  The sprint query string.</p>
<p>lane:        One of AcquisitionLane values.</p>
<p>seed_context: Optional NonfeedSeedContext from pivot/DuckDB extraction.</p>
<p></p>
<p>Returns:</p>
<p>Shaped query string, or a dict with lane guidance (e.g. {"_disabled": True}).</p>
<p>Returns {"_disabled": True} for BLOCKCHAIN when no crypto indicator present.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: acquisition_strategy.py, nonfeed_seed_runtime.py, plan_builder.py, prelude.py, sprint_scheduler_v1_archived.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (97)</summary>
<ul>
<li><code>run_enabled_acquisition_lanes</code> (__init__.py)
<details><summary>Run all enabled optional acquisition lanes (CT, WAYBACK, PASSIVE_DNS, BLOCKCHAIN)</summary>
<div class="doc-comment">
<p>Run all enabled optional acquisition lanes (CT, WAYBACK, PASSIVE_DNS, BLOCKCHAIN)</p>
<p>bounded by their per-lane plans from the acquisition strategy snapshot.</p>
<p></p>
<p>FEED and PUBLIC lanes are NOT run here — they are run by SprintScheduler</p>
<p>via its own pipeline calls.</p>
<p></p>
<p>STEALTH lane is NOT run here — caller must explicitly enable it.</p>
<p></p>
<p>Args:</p>
<p>snapshot:   AcquisitionStrategySnapshot from build_acquisition_plan().</p>
<p>query:      Sprint query string.</p>
<p>store:      DuckDBShadowStore for canonical storage (async_ingest_findings_batch).</p>
<p>uma_state:  Current UMA state ("ok" | "warn" | "critical" | "emergency").</p>
<p>seed_context: NonfeedSeedContext for domain/IP seeding.</p>
<p>graph_accumulator: SprintGraphAccumulator instance for graph wiring.</p>
<p>If None, graph accumulation is skipped (fail-soft, F265C).</p>
<p></p>
<p>Returns:</p>
<p>Tuple of AcquisitionLaneOutcome, one per optional lane.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- gather(return_exceptions=True) so one lane crash never fails others</p>
<p>- per-lane timeout enforced via asyncio.timeout</p>
<p>- per-lane max_items enforced by each lane adapter</p>
<p>- STEALTH never auto-enabled</p>
<p>- No MLX/model load</p>
</div>
</details>
</li>
<li><code>run_enabled_acquisition_lanes_streaming</code> (__init__.py)
<details><summary>P2-1: Streaming variant -- lanes run concurrently, yields per-lane as they complete.</summary>
<div class="doc-comment">
<p>P2-1: Streaming variant -- lanes run concurrently, yields per-lane as they complete.</p>
<p></p>
<p>Yields cumulative (outcome,) tuples so callers can accumulate incrementally.</p>
<p>Early-exit when min_finished lanes done (min_finished=0 means wait for all).</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- safe_gather_ok(return_exceptions=True) preserves fail-soft</p>
<p>- per-lane asyncio.timeout enforced</p>
<p>- STEALTH never auto-enabled</p>
<p>- M1 8GB safe: Semaphore(clearnet_max), bounded [1, 4] for M1 8GB safety</p>
</div>
</details>
</li>
<li><code>_build_plan_impl</code> (__init__.py) — <span class="doc-comment-inline">Internal implementation — raises on error (caller catches).</span></li>
<li><code>build_lane_query</code> (__init__.py)
<details><summary>Shape a source-specific query for an acquisition lane.</summary>
<div class="doc-comment">
<p>Shape a source-specific query for an acquisition lane.</p>
<p></p>
<p>F222I: When seed_context is provided (pivot/DuckDB domain extraction),</p>
<p>lanes receive the extracted domain/IP seed instead of the generic text query.</p>
<p>This enables CT/DOH/PassiveDNS/Wayback for "LockBit ransomware" style queries</p>
<p>that have no explicit domain/IP in the raw query text.</p>
<p></p>
<p>Rules per lane:</p>
<p>CT:          seed.domains[0] if available, else extract domains from base_query</p>
<p>WAYBACK:     seed.domains[0] or seed.urls[0] if available, else base_query</p>
<p>PASSIVE_DNS: seed.domains[0] or seed.ips[0] if available, else base_query</p>
<p>BLOCKCHAIN:  wallet/hash only; returns {"_disabled": True} if no crypto indicator</p>
<p>PUBLIC:      original query plus 1-2 bounded variants (seed ignored)</p>
<p>FEED:        original query unchanged (seed ignored)</p>
<p></p>
<p>No LLM, no network I/O. Deterministic.</p>
<p></p>
<p>Args:</p>
<p>base_query:  The sprint query string.</p>
<p>lane:        One of AcquisitionLane values.</p>
<p>seed_context: Optional NonfeedSeedContext from pivot/DuckDB extraction.</p>
<p></p>
<p>Returns:</p>
<p>Shaped query string, or a dict with lane guidance (e.g. {"_disabled": True}).</p>
<p>Returns {"_disabled": True} for BLOCKCHAIN when no crypto indicator present.</p>
</div>
</details>
</li>
<li><code>_build_nonfeed_lane_eligibility</code> (__init__.py)
<details><summary>F214: Build the nonfeed lane eligibility matrix for acquisition reporting.</summary>
<div class="doc-comment">
<p>F214: Build the nonfeed lane eligibility matrix for acquisition reporting.</p>
<p></p>
<p>Computed from query indicators (not plan.enabled) so the matrix explains WHY</p>
<p>each lane was or was not planned — using the same indicator logic as the</p>
<p>planner, independent of runtime state (hardware, transport, etc.).</p>
<p></p>
<p>Schema::</p>
<p></p>
<p>{</p>
<p>"public":  {"eligible": true, "reason": "...", "required_inputs": [], "available_inputs": {...}},</p>
<p>"ct":      {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},</p>
<p>"doh":     {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},</p>
<p>"wayback": {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},</p>
<p>"passive_dns": {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},  # noqa: E501</p>
<p>}</p>
<p></p>
<p>Profile rules (active300/default):</p>
<p>- public:   always eligible if provider available (advisory, not gated by candidates)</p>
<p>- CT:       eligible if domain (not IP-only) candidates present</p>
<p>- DOH:      eligible if domain (not IP-only) candidates present</p>
<p>- WAYBACK:  eligible if URL or domain candidates present</p>
<p>- passive_dns: eligible if domain or IP candidates present</p>
<p></p>
<p>Profile rules (nonfeed_diagnostic):</p>
<p>- public:   expected if provider available</p>
<p>- DOH:      expected if domains exist</p>
<p>- CT:       expected if domains exist</p>
<p>- WAYBACK:  expected if URLs/domains exist</p>
<p>- passive_dns: expected if domains/IPs exist</p>
</div>
</details>
</li>
<li><code>_run_ipfs_lane</code> (__init__.py)
<details><summary>R10: CID-only IPFS evidence fetch — bounded gateway fetch, no search/DHT/recursive.</summary>
<div class="doc-comment">
<p>R10: CID-only IPFS evidence fetch — bounded gateway fetch, no search/DHT/recursive.</p>
<p></p>
<p>Enabled only when query is an explicit CID. No model load, no browser,</p>
<p>no stealth. Hard cap: 5 CIDs per sprint regardless of max_items.</p>
</div>
</details>
</li>
<li><code>normalize_source_family_outcome</code> (__init__.py)
<details><summary>Normalize a raw lane or adapter outcome dict into SourceFamilyOutcome fields.</summary>
<div class="doc-comment">
<p>Normalize a raw lane or adapter outcome dict into SourceFamilyOutcome fields.</p>
<p></p>
<p>Handles three F207F shapes:</p>
<p>- AcquisitionLaneOutcome  (ct, wayback, passive_dns, blockchain lanes)</p>
<p>- dict with ct_results_raw / produced_items / accepted_findings keys</p>
<p>- Feed balance tuple (verdict_tag, signal, fallback_use, fallback_waste, quality)</p>
<p>which maps to family=FEED, attempted=True, raw_count=signal</p>
<p></p>
<p>Also handles the "missing family" case where no outcome was produced at all,</p>
<p>returning a skipped/attempted=False outcome for documentation purposes.</p>
</div>
</details>
</li>
<li><code>build_acquisition_plan</code> (__init__.py)
<details><summary>Build an acquisition strategy snapshot for the given sprint context.</summary>
<div class="doc-comment">
<p>Build an acquisition strategy snapshot for the given sprint context.</p>
<p></p>
<p>Args:</p>
<p>query:              The sprint query string.</p>
<p>duration_s:         Sprint duration in seconds.</p>
<p>aggressive_mode:    True if running in aggressive (parallel) mode.</p>
<p>uma_state:          Current UMA state string ("ok", "warn", "critical", "emergency").</p>
<p>swap_detected:      True if system swap has been detected.</p>
<p>accepted_findings_so_far: Number of accepted findings collected so far.</p>
<p>branch_timeout_count:    Number of branch timeouts in current sprint.</p>
<p>transport_authority_status: Optional dict with transport authority signals.</p>
<p>Supported keys:</p>
<p>- "degraded": bool — True if transport is degraded</p>
<p>- "stealth_phase": int — current stealth phase (1-4)</p>
<p>stealth_phase:      Optional dict with stealth phase info.</p>
<p>Supported keys:</p>
<p>- "phase": int — current stealth phase</p>
<p>- "breaker_seam_ready": bool — True when phase &gt;= 3</p>
<p>acquisition_profile: F216B: Runtime profile controlling lane caps.</p>
<p>"default" = standard behavior.</p>
<p>"nonfeed_diagnostic" = caps FEED at 25, enables nonfeed lanes for domain queries.</p>
<p>Falls back to HLEDAC_ACQUISITION_PROFILE env var if not explicitly passed.</p>
<p>rl_lane_combo: F265LANE: Optional frozenset of lane names (e.g. {"CT","WAYBACK"})</p>
<p>from RL policy action. When set, overrides lane enabled/disabled decisions</p>
<p>to match the RL-chosen combination.</p>
<p>feed_domain_seeds: P0-8: Domain seeds extracted from accepted feed findings.</p>
<p>When query has no domain indicator but feed findings contain domains,</p>
<p>these seeds enable CT/DOH/WAYBACK lanes mid-sprint.</p>
<p></p>
<p>Returns:</p>
<p>AcquisitionStrategySnapshot with per-lane plans.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O</p>
<p>- No model/MLX load</p>
<p>- No asyncio.run() / loop.run_until_complete()</p>
<p>- Bounded: max 12 lane plans (all canonical acquisition lanes)</p>
<p>- Fail-soft: on any error returns minimal snapshot with all lanes disabled</p>
</div>
</details>
</li>
<li><code>required_terminal_lanes</code> (__init__.py)
<details><summary>[F208A] Determine which lanes are mandatory for terminality.</summary>
<div class="doc-comment">
<p>[F208A] Determine which lanes are mandatory for terminality.</p>
<p></p>
<p>Rules:</p>
<p>- domain query + ok/warn memory: PUBLIC required, CT required</p>
<p>- domain query + critical: CT required (as attempted or explicit skip),</p>
<p>PUBLIC explicit skip allowed with memory_critical</p>
<p>- emergency: all non-feed lanes explicit skip with memory_emergency</p>
<p>- non-domain: CT not required (skip reason no_domain)</p>
<p>- STEALTH: never required by default</p>
<p>- FEED: not part of terminality guard</p>
<p></p>
<p>Args:</p>
<p>snapshot:    Current acquisition strategy snapshot.</p>
<p>query:       Sprint query string.</p>
<p>uma_state:   Current UMA state (ok, warn, critical, emergency).</p>
<p>swap_detected: True if swap has been detected.</p>
<p></p>
<p>Returns:</p>
<p>Tuple of MandatoryLaneTerminality, one per lane that has terminality requirements.</p>
</div>
</details>
</li>
<li><code>normalize_terminal_state</code> (__init__.py)
<details><summary>[F208L] Map an outcome dict to a canonical terminal state string.</summary>
<div class="doc-comment">
<p>[F208L] Map an outcome dict to a canonical terminal state string.</p>
<p></p>
<p>Supported terminal states:</p>
<p>- success       : attempted=True, accepted_count &gt; 0</p>
<p>- success_empty : attempted=True, raw_count &gt; 0, accepted_count = 0</p>
<p>- empty         : attempted=True, raw_count = 0, accepted_count = 0</p>
<p>- attempted     : attempted=True, no other qualifier</p>
<p>- skipped       : skipped=True</p>
<p>- error         : error is not None and not empty string</p>
<p>- timeout       : timeout=True</p>
<p></p>
<p>Non-terminal states (return as-is for identity check):</p>
<p>- pending</p>
<p>- running</p>
<p>- not_attempted</p>
<p>- missing</p>
<p>- ""  (empty string)</p>
<p>- None</p>
<p></p>
<p>accepted_count=0 alone does NOT make a lane non-terminal.</p>
<p>raw_count &gt; 0 with accepted_count = 0 normalizes to success_empty.</p>
<p>raw_count = 0 with attempted = True normalizes to empty.</p>
</div>
</details>
</li>
<li><code>build_snapshot</code> (__init__.py)
<details><summary>Build a NonfeedMissionSnapshot from current scheduler state.</summary>
<div class="doc-comment">
<p>Build a NonfeedMissionSnapshot from current scheduler state.</p>
<p></p>
<p>Args:</p>
<p>acquisition_profile: Current acquisition profile name</p>
<p>acquisition_lane_outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes</p>
<p>public_outcome: _public_outcome dict from SprintScheduler (None if PUBLIC never ran)</p>
<p>ct_quarantine_count: ct_quarantine_count from SprintSchedulerResult</p>
<p>quality_rejection_ledger: quality_rejection_ledger from SprintSchedulerResult</p>
<p>memory_skipped_families: Families skipped due to memory pressure</p>
</div>
</details>
</li>
<li><code>_run_wayback_lane</code> (__init__.py)
<details><summary>Run Wayback diff mining lane — runtime safety check before network call.</summary>
<div class="doc-comment">
<p>Run Wayback diff mining lane — runtime safety check before network call.</p>
<p></p>
<p>[F207K-A] Uses bridge helpers to produce CanonicalFinding candidates</p>
<p>with rejection tracking.</p>
</div>
</details>
</li>
<li><code>_run_pdns_lane</code> (__init__.py)
<details><summary>Run passive DNS lookup lane — wired to call_lookup_passive_dns with domain/IP shaping.</summary>
<div class="doc-comment">
<p>Run passive DNS lookup lane — wired to call_lookup_passive_dns with domain/IP shaping.</p>
<p></p>
<p>[F207K-A] Uses bridge helpers to produce CanonicalFinding candidates</p>
<p>with rejection tracking.</p>
</div>
</details>
</li>
<li><code>canonicalize_source_family_outcomes</code> (__init__.py)
<details><summary>Deduplicate and merge source family outcomes that normalize to the same family.</summary>
<div class="doc-comment">
<p>Deduplicate and merge source family outcomes that normalize to the same family.</p>
<p></p>
<p>When multiple outcomes normalize to the same family name (e.g., "CT" and "ct"),</p>
<p>they are merged into a single outcome using the merge rules:</p>
<p>- attempted = any(attempted=True)</p>
<p>- skipped   = all(skipped) only if no outcome was attempted; otherwise False</p>
<p>- timeout   = any(timeout=True)</p>
<p>- error     = prefer real provider/runtime error over synthetic "no_candidates"</p>
<p>- terminal_state = highest-priority from TERMINAL_PRIORITY table</p>
<p>- raw_count / built_count / accepted_count = max of all</p>
<p>- duration_s = max non-null duration</p>
</div>
</details>
</li>
<li><code>_run_ct_lane</code> (__init__.py)
<details><summary>Run CT/crt.sh lane — wired to call_crtsh() for measurable outcome.</summary>
<div class="doc-comment">
<p>Run CT/crt.sh lane — wired to call_crtsh() for measurable outcome.</p>
<p></p>
<p>[F207K-A] Uses bridge helpers to produce CanonicalFinding candidates</p>
<p>with rejection tracking. DB write is the lane runner's job (not adapter).</p>
</div>
</details>
</li>
<li><code>_run_doh_lane</code> (__init__.py)
<details><summary>Run DOH lane — DNS-over-HTTPS passive DNS recon via DOHAdapter.</summary>
<div class="doc-comment">
<p>Run DOH lane — DNS-over-HTTPS passive DNS recon via DOHAdapter.</p>
<p></p>
<p>F222B: First-class nonfeed lane. No model load, no browser, no stealth.</p>
<p>Bounds: max_items=20, timeout_s=30, concurrency=2.</p>
<p>Fail-soft: provider errors never break other lanes.</p>
</div>
</details>
</li>
<li><code>_disabled_reason</code> (__init__.py) — <span class="doc-comment-inline">Return the disabled-reason string for a lane, matching original inline logic.</span></li>
<li><code>terminality_report</code> (__init__.py)
<details><summary>[F208A] Produce a terminality report comparing required vs observed lane states.</summary>
<div class="doc-comment">
<p>[F208A] Produce a terminality report comparing required vs observed lane states.</p>
<p></p>
<p>Args:</p>
<p>required_lanes:    Tuple of MandatoryLaneTerminality from required_terminal_lanes().</p>
<p>observed_outcomes: Tuple of outcome dicts (from AcquisitionLaneOutcome.to_dict()).</p>
<p></p>
<p>Returns:</p>
<p>Dict with:</p>
<p>checked: list of lane names checked</p>
<p>satisfied: list of lane names with terminal outcomes</p>
<p>required_lanes: list of mandatory lane specs</p>
<p>terminal_lanes: list of lanes at terminal state</p>
<p>missing_lanes: list of mandatory lanes NOT at terminal state</p>
<p>skipped_lanes: list of lanes that were skipped</p>
<p>errors: list of lanes with errors</p>
<p>reasons: dict mapping lane → terminality reason string</p>
</div>
</details>
</li>
<li><code>_get_lane_outcome</code> (__init__.py)
<details><summary>Get the outcome dict for a lane family.</summary>
<div class="doc-comment">
<p>Get the outcome dict for a lane family.</p>
<p></p>
<p>Returns a dict with keys: accepted_findings, terminal_state, error, skipped</p>
<p>suitable for mission evaluation.</p>
<p></p>
<p>Args:</p>
<p>family: Lane family string (PUBLIC, CT, etc.)</p>
<p>acquisition_lane_outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes</p>
<p>public_outcome: _public_outcome dict from SprintScheduler (for PUBLIC lane)</p>
<p>ct_quarantine_count: ct_quarantine_count from SprintSchedulerResult</p>
<p>quality_rejection_ledger: quality_rejection_ledger from SprintSchedulerResult</p>
</div>
</details>
</li>
<li><code>select_ct_domains_for_passivedns_pivot</code> (__init__.py)
<details><summary>Sprint R5: Extract deduplicated domains from CT-accepted CanonicalFinding</summary>
<div class="doc-comment">
<p>Sprint R5: Extract deduplicated domains from CT-accepted CanonicalFinding</p>
<p>candidates for PassiveDNS one-hop pivot.</p>
<p></p>
<p>Pure function: deterministic output from deterministic input.</p>
<p>No network I/O, no side effects.</p>
<p></p>
<p>Args:</p>
<p>ct_candidate_findings: List of CanonicalFinding (or dict-like) objects</p>
<p>with source_type="ct" and payload_text containing domain lines.</p>
<p>max_pivots: Default cap on pivot domains (default=5, hard_max=10).</p>
<p></p>
<p>Returns:</p>
<p>Deduplicated list of domain strings (max 10), in first-seen order.</p>
<p></p>
<p>Invariants:</p>
<p>- pivot depth = 1 (caller enforces)</p>
<p>- no recursive pivoting</p>
<p>- no network I/O</p>
<p>- no new queue framework</p>
<p>- deterministic: same input always yields same output</p>
<p></p>
<p>Domain extraction:</p>
<p>- Parse "domain: &lt;value&gt;" lines from payload_text</p>
<p>- Fallback: query field if no domain line found</p>
<p>- Skip: empty/whitespace-only domains</p>
<p>- Order: first-seen (dict.fromkeys preserves insertion order)</p>
</div>
</details>
</li>
<li><code>_run_academic_lane</code> (__init__.py) — <span class="doc-comment-inline">Run academic search lane — R9: bounded, research-profile-only, no query expansion.</span></li>
<li><code>_run_wayback_lane</code> (__init__.py)</li>
<li><code>_run_pdns_lane</code> (__init__.py)</li>
<li><code>_run_blockchain_lane</code> (__init__.py) — <span class="doc-comment-inline">Run blockchain forensics lane.</span></li>
<li><code>_run_ct_lane</code> (__init__.py)</li>
<li><code>_extract_domain_from_ct_finding</code> (__init__.py)
<details><summary>Extract domain from a CT CanonicalFinding (or dict-like) object.</summary>
<div class="doc-comment">
<p>Extract domain from a CT CanonicalFinding (or dict-like) object.</p>
<p></p>
<p>Strategy:</p>
<p>1. Try payload_text: parse "domain: &lt;value&gt;" lines</p>
<p>2. Fallback: query field</p>
<p></p>
<p>Returns:</p>
<p>Normalized lowercase domain string, or None if not extractable.</p>
</div>
</details>
</li>
<li><code>_run_open_source_lane</code> (__init__.py) — <span class="doc-comment-inline">Run OpenSourceCollectors lane — pastebin, usenet, matrix, academic, sec_edgar, court records.</span></li>
<li><code>_run_shodan_lane</code> (__init__.py) — <span class="doc-comment-inline">Run Shodan intelligence lane — device/IP fingerprints.</span></li>
<li><code>lane_is_terminal</code> (__init__.py)
<details><summary>[F208A] Return True if the lane outcome is in a terminal state.</summary>
<div class="doc-comment">
<p>[F208A] Return True if the lane outcome is in a terminal state.</p>
<p></p>
<p>Terminal states:</p>
<p>- attempted=True (lane ran at least once)</p>
<p>- skipped=True (lane was intentionally skipped)</p>
<p>- error is not None (lane encountered an error)</p>
<p>- timeout=True (lane exceeded its time limit)</p>
</div>
</details>
</li>
<li><code>_evaluate_family_status</code> (__init__.py)
<details><summary>Evaluate the mission status of a single family.</summary>
<div class="doc-comment">
<p>Evaluate the mission status of a single family.</p>
<p></p>
<p>Returns one of: accepted, terminal, provider_failure, memory_skip, pending, missing</p>
</div>
</details>
</li>
<li><code>infer_mission_intent</code> (__init__.py)
<details><summary>F225A: Infer mission intent from query string.</summary>
<div class="doc-comment">
<p>F225A: Infer mission intent from query string.</p>
<p></p>
<p>Rules:</p>
<p>- CVE-* pattern          → cve_recon</p>
<p>- crypto wallet/hash     → wallet_recon</p>
<p>- email-like indicator   → person_recon</p>
<p>- domain/IP/URL         → domain_recon / infra_recon</p>
<p>- otherwise             → unknown (safe lanes only)</p>
<p></p>
<p>Returns a string constant from MissionIntent.</p>
<p>No network I/O, no model load. Deterministic.</p>
</div>
</details>
</li>
<li><code>_run_censys_lane</code> (__init__.py) — <span class="doc-comment-inline">Run Censys intelligence lane — certificate transparency.</span></li>
<li><code>_run_greynoise_lane</code> (__init__.py) — <span class="doc-comment-inline">Run GreyNoise intelligence lane — mass scanner classification.</span></li>
<li><code>normalize_passive_dns_query</code> (__init__.py)
<details><summary>Shape a PassiveDNS query with fallback domain extraction from raw query.</summary>
<div class="doc-comment">
<p>Shape a PassiveDNS query with fallback domain extraction from raw query.</p>
<p></p>
<p>F265: When seed_context.domains is empty (PUBLIC lane NameError caused</p>
<p>domain seeds to never populate), fall back to extracting a domain directly</p>
<p>from the raw query using the same regex used elsewhere in build_lane_query.</p>
<p></p>
<p>Returns:</p>
<p>First domain/IP indicator found, or "" if nothing extractable.</p>
</div>
</details>
</li>
<li><code>_derive_terminal</code> (__init__.py)</li>
<li><code>_extract_cids_from_text</code> (__init__.py) — <span class="doc-comment-inline">Extract unique explicit CIDs from arbitrary text. Bounded dedup.</span></li>
<li><code>_mission_lanes</code> (__init__.py)
<details><summary>F225A: Derive required and optional lanes from mission intent.</summary>
<div class="doc-comment">
<p>F225A: Derive required and optional lanes from mission intent.</p>
<p></p>
<p>Returns (required_lanes, optional_lanes).</p>
<p>Lane priority/reason adjustments only — all safety gates preserved.</p>
</div>
</details>
</li>
<li><code>_wallet_to_findings</code> (__init__.py) — <span class="doc-comment-inline">Convert blockchain WalletAnalysis to CanonicalFinding list.</span></li>
<li><code>_looks_like_domain</code> (__init__.py) — <span class="doc-comment-inline">Return True if value looks like a domain name (no IP, has TLD).</span></li>
<li><code>normalize_source_family_name</code> (__init__.py)
<details><summary>Normalize a source family name to its canonical lowercase form.</summary>
<div class="doc-comment">
<p>Normalize a source family name to its canonical lowercase form.</p>
<p></p>
<p>Maps mixed-case variants to their canonical lowercase representation so that</p>
<p>"CT", "ct", "Ct" all resolve to "ct", preventing duplicate outcomes for the same</p>
<p>logical family in a single acquisition report.</p>
<p></p>
<p>Canonical families: feed, public, ct, wayback, passive_dns, academic, ipfs, pivot.</p>
</div>
</details>
</li>
<li><code>_derive_exit_reason</code> (__init__.py) — <span class="doc-comment-inline">Derive the canonical mission exit reason.</span></li>
<li><code>_hits_to_ct_findings</code> (__init__.py) — <span class="doc-comment-inline">Convert crt.sh DiscoveryHit tuple to CanonicalFinding list.</span></li>
<li><code>_ips_to_pdns_findings</code> (__init__.py) — <span class="doc-comment-inline">Convert passive DNS IP list to CanonicalFinding list.</span></li>
<li><code>is_lane_enabled</code> (__init__.py)
<details><summary>Return True if the given lane is enabled in the acquisition plan.</summary>
<div class="doc-comment">
<p>Return True if the given lane is enabled in the acquisition plan.</p>
<p></p>
<p>Fail-soft: returns False if snapshot is None or lane is not found.</p>
</div>
</details>
</li>
<li><code>get_lane_plan</code> (__init__.py)
<details><summary>Return the AcquisitionLanePlan for the given lane, or None if not found.</summary>
<div class="doc-comment">
<p>Return the AcquisitionLanePlan for the given lane, or None if not found.</p>
<p></p>
<p>Fail-soft: returns None if snapshot is None or lane is not found.</p>
</div>
</details>
</li>
<li><code>lane_skip_reason</code> (__init__.py)
<details><summary>Return the skip reason for the given lane, or None if lane is enabled or not found.</summary>
<div class="doc-comment">
<p>Return the skip reason for the given lane, or None if lane is enabled or not found.</p>
<p></p>
<p>Fail-soft: returns None if snapshot is None or lane is not found.</p>
</div>
</details>
</li>
<li><code>mark_warmup_done</code> (lifecycle.py)
<details><summary>F184A: Canonical public API for WARMUP-&gt;ACTIVE transition.</summary>
<div class="doc-comment">
<p>F184A: Canonical public API for WARMUP-&gt;ACTIVE transition.</p>
<p></p>
<p>F184A: Replaces direct adapter._lc.mark_warmup_done() bypass in run().</p>
</div>
</details>
</li>
<li><code>_pick_best_terminal</code> (__init__.py) — <span class="doc-comment-inline">Pick the highest-priority terminal_state from a list of same-family outcomes.</span></li>
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
<li><code>_has_explicit_cid</code> (__init__.py) — <span class="doc-comment-inline">Return True if value is an explicit IPFS CID (CIDv0 or CIDv1 base32).</span></li>
<li><code>_lc</code> (__init__.py) — <span class="doc-comment-inline">Apply lane-specific concurrency adjustments on top of base.</span></li>
<li><code>_base_concurrency</code> (__init__.py) — <span class="doc-comment-inline">Return base concurrency based on hardware state.</span></li>
<li><code>_lane_concurrency</code> (__init__.py) — <span class="doc-comment-inline">Apply lane-specific adjustments on top of base concurrency.</span></li>
<li><code>_extract_crypto_from_query</code> (__init__.py) — <span class="doc-comment-inline">Extract crypto wallet addresses and hashes from query string.</span></li>
<li><code>is_terminal</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: is_terminal(). Returns True when phase is TEARDOWN.</span></li>
<li><code>_current_phase</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: _current_phase (SprintPhase enum). utils: state (SprintLifecycleState).</span></li>
<li><code>request_abort</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: request_abort(reason).</span></li>
<li><code>_has_threat_indicator</code> (__init__.py)
<details><summary>Return True if query contains threat/crime indicators suggesting active investigation.</summary>
<div class="doc-comment">
<p>Return True if query contains threat/crime indicators suggesting active investigation.</p>
<p></p>
<p>Used by required_terminal_lanes() to ensure PUBLIC lane is mandatory for threat</p>
<p>queries even when no domain/IP is present — PUBLIC can discover infrastructure</p>
<p>from text search results alone (no seeds required).</p>
</div>
</details>
</li>
<li><code>tick</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: tick() returns SprintPhase. Fallback: 'UNKNOWN' phase string.</span></li>
<li><code>release</code> (types.py)</li>
<li><code>__post_init__</code> (__init__.py)</li>
<li><code>_get_ct_adapter</code> (__init__.py) — <span class="doc-comment-inline">Return the CT adapter: real call_crtsh or the patched fake.</span></li>
<li><code>start</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: start() -- transitions BOOT-&gt;WARMUP.</span></li>
<li><code>remaining_time</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: remaining_time(). utils: remaining_time property.</span></li>
<li><code>recommended_tool_mode</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: recommended_tool_mode(). Returns 'normal'/'prune'/'panic'.</span></li>
<li><code>_abort_requested</code> (lifecycle.py)</li>
<li><code>_abort_reason</code> (lifecycle.py)</li>
<li><code>is_mission_profile</code> (__init__.py) — <span class="doc-comment-inline">Return True when the profile is any nonfeed_diagnostic variant.</span></li>
<li><code>set_pre_loop_cost_s</code> (lifecycle.py) — <span class="doc-comment-inline">F288: Set pre_loop_cost_s on the underlying lifecycle if supported.</span></li>
<li><code>set_first_cycle_ran</code> (lifecycle.py) — <span class="doc-comment-inline">F290: Signal that first acquisition cycle has completed.</span></li>
<li><code>allocate</code> (types.py)</li>
<li><code>_extract_ips_from_query</code> (__init__.py) — <span class="doc-comment-inline">Extract IP address strings from query.</span></li>
<li><code>kind_counts</code> (__init__.py) — <span class="doc-comment-inline">Return counts by non-empty seed kind.</span></li>
<li><code>get_required_families</code> (__init__.py) — <span class="doc-comment-inline">Required lane families for nonfeed_diagnostic mission.</span></li>
<li><code>get_optional_families</code> (__init__.py) — <span class="doc-comment-inline">Optional lane families for nonfeed_diagnostic mission.</span></li>
<li><code>_family_to_lane</code> (__init__.py) — <span class="doc-comment-inline">Map lane family string to AcquisitionLane constant.</span></li>
<li><code>_mission_target_kind</code> (__init__.py) — <span class="doc-comment-inline">F225A: Derive target kind from mission intent.</span></li>
<li><code>_has_crypto_wallet</code> (__init__.py)</li>
<li><code>_stealth_never_run</code> (__init__.py) — <span class="doc-comment-inline">STEALTH is never auto-run — always record the skip.</span></li>
<li><code>_looks_like_ip</code> (__init__.py) — <span class="doc-comment-inline">Return True if string looks like an IP address.</span></li>
<li><code>consume</code> (types.py)</li>
<li><code>timeout</code> (types.py)</li>
<li><code>_lane_rule</code> (__init__.py)</li>
<li><code>has_domain</code> (__init__.py)</li>
<li><code>has_ip</code> (__init__.py)</li>
<li><code>has_url</code> (__init__.py)</li>
<li><code>to_dict</code> (__init__.py)</li>
<li><code>to_dict</code> (__init__.py)</li>
<li><code>to_dict</code> (__init__.py)</li>
<li><code>_has_domain_or_ip</code> (__init__.py)</li>
<li><code>_has_url</code> (__init__.py)</li>
<li><code>_has_crypto_hash</code> (__init__.py)</li>
<li><code>_has_crypto_indicator</code> (__init__.py)</li>
<li><code>_stealth_never_run</code> (__init__.py)</li>
<li><code>__init__</code> (lifecycle.py)</li>
<li><code>total_allocated</code> (types.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (25)</summary>
<ul>
<li><code>NonfeedMissionController</code> (__init__.py)
<details><summary>F217B: Canonical nonfeed mission contract for nonfeed_diagnostic profile.</summary>
<div class="doc-comment">
<p>F217B: Canonical nonfeed mission contract for nonfeed_diagnostic profile.</p>
<p></p>
<p>Coordinates lane family expectations without benchmark-owned logic.</p>
<p>For acquisition_profile=nonfeed_diagnostic:</p>
<p>- Required lane families: PUBLIC, CT, PIVOT_EXECUTOR</p>
<p>- Optional lane families: WAYBACK, PASSIVE_DNS</p>
<p>- FEED is capped until required nonfeed lanes are terminal</p>
<p>- Mission finishes only when each required family has:</p>
<p>accepted evidence</p>
<p>OR explicit terminal state</p>
<p>OR explicit provider failure</p>
<p>OR explicit memory skip</p>
<p></p>
<p>IMPORTANT — what does NOT count as accepted evidence:</p>
<p>- CT quarantine is NOT accepted evidence (raw hits rejected by bridge criteria)</p>
<p>- Quality rejection ledger is NOT accepted evidence (quality gate rejection)</p>
<p>- PUBLIC explicit failure (FETCH_ZERO_SUCCESS, QUALITY_REJECTED, etc.) counts</p>
<p>as terminal but NOT accepted</p>
<p>- Feed findings do NOT satisfy nonfeed mission</p>
</div>
</details>
</li>
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
<li><code>NonfeedSeedContext</code> (__init__.py)
<details><summary>F222I: Bounded seed context for nonfeed lane query shaping.</summary>
<div class="doc-comment">
<p>F222I: Bounded seed context for nonfeed lane query shaping.</p>
<p></p>
<p>Produced by pivot planner / DuckDB seed extraction from text query.</p>
<p>Threaded into build_lane_query so lanes receive deterministic domain/IP</p>
<p>seeds instead of the generic text query.</p>
<p></p>
<p>Bounds:</p>
<p>- max_domains=10, max_ips=10, max_urls=10 — hard caps</p>
<p>- All fields are tuples (immutable, hashable)</p>
<p>- Publisher domains (source URL hostnames) are excluded from seeds</p>
<p></p>
<p>Lane shaping rules:</p>
<p>CT:          domains[0] if available, else empty</p>
<p>DOH:         domains[0] if available, else _disabled</p>
<p>WAYBACK:     domains[0] or URLs[0] if available</p>
<p>PASSIVE_DNS: domains[0] or IPs[0] if available</p>
<p>PUBLIC:      unchanged (original text query)</p>
<p>FEED:        unchanged</p>
</div>
</details>
</li>
<li><code>NonfeedPlanDebug</code> (__init__.py)
<details><summary>[F207L] Diagnostic snapshot of nonfeed lane planning for live KPI debugging.</summary>
<div class="doc-comment">
<p>[F207L] Diagnostic snapshot of nonfeed lane planning for live KPI debugging.</p>
<p></p>
<p>Records what the acquisition planner decided and why,</p>
<p>so live KPI can diagnose nonfeed_attempted=0 root cause.</p>
<p>F227D: Mutable so scheduler can annotate cap reason during sprint execution.</p>
</div>
</details>
</li>
<li><code>LaneBudgetPool</code> (types.py) — <span class="doc-comment-inline">Per-lane timeout accounting pool.</span></li>
<li><code>AcquisitionLaneOutcome</code> (__init__.py)</li>
<li><code>AcquisitionContext</code> (__init__.py) — <span class="doc-comment-inline">Derived flags bundle for lane planning — constructed once per _build_plan_impl call.</span></li>
<li><code>SourceFamilyOutcome</code> (__init__.py)
<details><summary>Normalized outcome for one source family (lane) in the scheduler report.</summary>
<div class="doc-comment">
<p>Normalized outcome for one source family (lane) in the scheduler report.</p>
<p></p>
<p>F207G: Unifies CTOutcome, PassiveDNSOutcome, WaybackDiffResult, and feed</p>
<p>balance telemetry into one canonical shape so diagnostics have a single</p>
<p>place to explain per-family zero-yield.</p>
</div>
</details>
</li>
<li><code>NonfeedMissionSnapshot</code> (__init__.py)
<details><summary>F217B: Snapshot of nonfeed mission controller state at a point in time.</summary>
<div class="doc-comment">
<p>F217B: Snapshot of nonfeed mission controller state at a point in time.</p>
<p></p>
<p>This is a plain dataclass (not frozen) so that the scheduler can</p>
<p>accumulate state over the sprint lifetime.</p>
</div>
</details>
</li>
<li><code>AcquisitionLane</code> (__init__.py)</li>
<li><code>AcquisitionStrategySnapshot</code> (__init__.py) — <span class="doc-comment-inline">Full acquisition strategy snapshot for one sprint/cycle.</span></li>
<li><code>MissionIntent</code> (__init__.py)
<details><summary>F225A: Lightweight mission intent classification.</summary>
<div class="doc-comment">
<p>F225A: Lightweight mission intent classification.</p>
<p></p>
<p>Additive telemetry — does NOT change lane enable/disable logic.</p>
<p>Does NOT bypass UMA/hardware safety, enable stealth/browser,</p>
<p>or increase network aggressiveness.</p>
</div>
</details>
</li>
<li><code>CTLossStage</code> (types.py) — <span class="doc-comment-inline">Enum describing where CT raw evidence is lost in the live bridge path.</span></li>
<li><code>RiskLevel</code> (__init__.py)
<details><summary>Risk levels for acquisition lane planning.</summary>
<div class="doc-comment">
<p>Risk levels for acquisition lane planning.</p>
<p></p>
<p>Inherits from `str` so the enum members are also `str` instances —</p>
<p>preserves the existing `risk_level: str = RiskLevel.MEDIUM` field</p>
<p>type without forcing all callers to migrate. Values match canonical</p>
<p>`project_types.RiskLevel` (lowercase).</p>
</div>
</details>
</li>
<li><code>LaneRule</code> (__init__.py)
<details><summary>Table-driven lane planning rule.</summary>
<div class="doc-comment">
<p>Table-driven lane planning rule.</p>
<p></p>
<p>One rule per AcquisitionLane.  The enabled/reason/concurrency logic</p>
<p>is expressed as pure functions of AcquisitionContext so the full</p>
<p>decision table is visible and auditable in one place.</p>
</div>
</details>
</li>
<li><code>MandatoryLaneTerminality</code> (__init__.py)
<details><summary>[F208A] Canonical terminality contract for mandatory lanes.</summary>
<div class="doc-comment">
<p>[F208A] Canonical terminality contract for mandatory lanes.</p>
<p></p>
<p>A mandatory lane must reach a terminal state (attempted, skipped, error, timeout)</p>
<p>before a sprint is considered complete. This dataclass defines the contract.</p>
</div>
</details>
</li>
<li><code>EarlyExitClass</code> (types.py) — <span class="doc-comment-inline">Sprint F215D: Canonical early exit classification for sprint runs.</span></li>
<li><code>MissionTargetKind</code> (__init__.py) — <span class="doc-comment-inline">F225A: Target kind derived from query analysis.</span></li>
<li><code>AcquisitionLanePlan</code> (__init__.py) — <span class="doc-comment-inline">Plan for one acquisition lane.</span></li>
<li><code>FeedDominanceGuardResult</code> (types.py) — <span class="doc-comment-inline">F214: Result of FeedDominanceGuard.compute().</span></li>
<li><code>NonfeedMissionExitReason</code> (__init__.py) — <span class="doc-comment-inline">F217B: Canonical mission exit reason values.</span></li>
<li><code>SourceTier</code> (types.py) — <span class="doc-comment-inline">Feed source priority tier.</span></li>
<li><code>SourceTier</code> (config.py) — <span class="doc-comment-inline">Feed source priority tier.</span></li>
<li><code>LaneBudgetAllocation</code> (types.py)</li>
<li><code>LaneSpec</code> (__init__.py) — <span class="doc-comment-inline">Static per-lane execution constants.</span></li>
</ul>
</details>

<details><summary><strong>Method</strong> (36)</summary>
<ul>
<li><code>build_snapshot</code> (__init__.py)
<details><summary>Build a NonfeedMissionSnapshot from current scheduler state.</summary>
<div class="doc-comment">
<p>Build a NonfeedMissionSnapshot from current scheduler state.</p>
<p></p>
<p>Args:</p>
<p>acquisition_profile: Current acquisition profile name</p>
<p>acquisition_lane_outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes</p>
<p>public_outcome: _public_outcome dict from SprintScheduler (None if PUBLIC never ran)</p>
<p>ct_quarantine_count: ct_quarantine_count from SprintSchedulerResult</p>
<p>quality_rejection_ledger: quality_rejection_ledger from SprintSchedulerResult</p>
<p>memory_skipped_families: Families skipped due to memory pressure</p>
</div>
</details>
</li>
<li><code>_get_lane_outcome</code> (__init__.py)
<details><summary>Get the outcome dict for a lane family.</summary>
<div class="doc-comment">
<p>Get the outcome dict for a lane family.</p>
<p></p>
<p>Returns a dict with keys: accepted_findings, terminal_state, error, skipped</p>
<p>suitable for mission evaluation.</p>
<p></p>
<p>Args:</p>
<p>family: Lane family string (PUBLIC, CT, etc.)</p>
<p>acquisition_lane_outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes</p>
<p>public_outcome: _public_outcome dict from SprintScheduler (for PUBLIC lane)</p>
<p>ct_quarantine_count: ct_quarantine_count from SprintSchedulerResult</p>
<p>quality_rejection_ledger: quality_rejection_ledger from SprintSchedulerResult</p>
</div>
</details>
</li>
<li><code>_evaluate_family_status</code> (__init__.py)
<details><summary>Evaluate the mission status of a single family.</summary>
<div class="doc-comment">
<p>Evaluate the mission status of a single family.</p>
<p></p>
<p>Returns one of: accepted, terminal, provider_failure, memory_skip, pending, missing</p>
</div>
</details>
</li>
<li><code>_derive_exit_reason</code> (__init__.py) — <span class="doc-comment-inline">Derive the canonical mission exit reason.</span></li>
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
<li><code>__post_init__</code> (__init__.py)</li>
<li><code>start</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: start() -- transitions BOOT-&gt;WARMUP.</span></li>
<li><code>remaining_time</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: remaining_time(). utils: remaining_time property.</span></li>
<li><code>recommended_tool_mode</code> (lifecycle.py) — <span class="doc-comment-inline">runtime: recommended_tool_mode(). Returns 'normal'/'prune'/'panic'.</span></li>
<li><code>_abort_requested</code> (lifecycle.py)</li>
<li><code>_abort_reason</code> (lifecycle.py)</li>
<li><code>is_mission_profile</code> (__init__.py) — <span class="doc-comment-inline">Return True when the profile is any nonfeed_diagnostic variant.</span></li>
<li><code>set_pre_loop_cost_s</code> (lifecycle.py) — <span class="doc-comment-inline">F288: Set pre_loop_cost_s on the underlying lifecycle if supported.</span></li>
<li><code>set_first_cycle_ran</code> (lifecycle.py) — <span class="doc-comment-inline">F290: Signal that first acquisition cycle has completed.</span></li>
<li><code>allocate</code> (types.py)</li>
<li><code>kind_counts</code> (__init__.py) — <span class="doc-comment-inline">Return counts by non-empty seed kind.</span></li>
<li><code>get_required_families</code> (__init__.py) — <span class="doc-comment-inline">Required lane families for nonfeed_diagnostic mission.</span></li>
<li><code>get_optional_families</code> (__init__.py) — <span class="doc-comment-inline">Optional lane families for nonfeed_diagnostic mission.</span></li>
<li><code>_family_to_lane</code> (__init__.py) — <span class="doc-comment-inline">Map lane family string to AcquisitionLane constant.</span></li>
<li><code>consume</code> (types.py)</li>
<li><code>timeout</code> (types.py)</li>
<li><code>has_domain</code> (__init__.py)</li>
<li><code>has_ip</code> (__init__.py)</li>
<li><code>has_url</code> (__init__.py)</li>
<li><code>to_dict</code> (__init__.py)</li>
<li><code>to_dict</code> (__init__.py)</li>
<li><code>to_dict</code> (__init__.py)</li>
<li><code>__init__</code> (lifecycle.py)</li>
<li><code>total_allocated</code> (types.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (27)</summary>
<ul>
<li><code>_TIER_ORDER</code> (config.py)</li>
<li><code>ACQUISITION_REPORT_SCHEMA_VERSION</code> (__init__.py)</li>
<li><code>_CIDV0_RE</code> (__init__.py)</li>
<li><code>_CIDV1_BASE32_RE</code> (__init__.py)</li>
<li><code>_MISSION_FEED_CAP_THRESHOLDS</code> (__init__.py)</li>
<li><code>_NONFEED_PROFILE_FEED_CAP_THRESHOLDS</code> (__init__.py)</li>
<li><code>_MISSION_FEED_CAP_THRESHOLDS</code> (__init__.py)</li>
<li><code>_NONFEED_PROFILE_FEED_CAP_THRESHOLDS</code> (__init__.py)</li>
<li><code>LANE_RULES</code> (__init__.py)</li>
<li><code>TERMINAL_STATES</code> (__init__.py)</li>
<li><code>NON_TERMINAL_STATES</code> (__init__.py)</li>
<li><code>_TERMINAL_PRIORITY</code> (__init__.py)</li>
<li><code>_NONFEED_LANE_FAMILY_MAP</code> (__init__.py)</li>
<li><code>_ACCEPTED_TERMINAL_STATES</code> (__init__.py)</li>
<li><code>_DOMAIN_OR_IP_RE</code> (__init__.py)</li>
<li><code>_URL_RE</code> (__init__.py)</li>
<li><code>_WALLET_RE</code> (__init__.py)</li>
<li><code>_CRYPTO_HASH_RE</code> (__init__.py)</li>
<li><code>_CVE_RE</code> (__init__.py)</li>
<li><code>_SAFE_LANES</code> (__init__.py)</li>
<li><code>_SAFE_OPTIONAL</code> (__init__.py)</li>
<li><code>_MISSION_TARGET_KIND</code> (__init__.py)</li>
<li><code>_THREAT_INDICATOR_RE</code> (__init__.py)</li>
<li><code>_LANE_TO_FAMILY</code> (__init__.py)</li>
<li><code>_NONFEED_SEED_EMPTY</code> (__init__.py)</li>
<li><code>_TIER_ORDER</code> (types.py)</li>
<li><code>_DEFAULT_SOURCE_TIER_MAP</code> (types.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 5 |
| Total lines | 2698 |
| Avg lines/file | 539 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |


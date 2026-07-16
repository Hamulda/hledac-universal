+++
title = "runtime/acquisition/"
weight = 15
description = "<think> Let me analyze the module overview for `runtime/acquisition` based on the provided structural context.  Key observations: 1. The module has 14 Python files with 3321 total lines (237 avg per f..."

[extra]
tier = 2
file_count = 14
total_lines = 3321
languages = "Python"
parent_path = "runtime"
+++

<think>
Let me analyze the module overview for `runtime/acquisition` based on the provided structural context.

Key observations:
1. The module has 14 Python files with 3321 total lines (237 avg per file)
2. No outgoing or incoming dependencies detected - this is interesting
3. Key symbols include `AcquisitionLane`, `build_acquisition_plan`, `build_acquisition_report`, `FeedDominanceBudget`, and `NonfeedMissionController`
4. The module seems to handle acquisition planning, lane execution, budget management, and mission control
5. It includes various functions for eligibility checking, report building, domain expansion, and threat dictionary lookups

The module appears to:
- Define acquisition lanes (constants and enums)
- Build acquisition plans based on various parameters
- Manage feed dominance budgets
- Coordinate nonfeed missions
- Handle eligibility and terminal states
- Generate reports
- Expand domains from keywords
- Detect CIDs
- Manage lane plans with concurrency and timeout settings

Now let me write the module overview following the rules:
- First sentence must state what the module DOES or its PURPOSE
- Focus on purpose, responsibilities, and architectural role
- Describe architectural role (hub, leaf, bridge)
- Explain how it fits into the larger system
- Note complexity and scale
- Don't enumerate specific files or classes unnecessarily
- 4-8 sentences
- Don't start with variations of "The X module consists of..."
- Vary sentence structure
- Be specific about what it does

The module seems to be a leaf node in the dependency graph (no incoming deps detected, though this seems unusual given the number of internal references). Let me reconsider - the dependency graph shows no incoming or outgoing dependencies, which is unusual. But the symbol references suggest it IS referenced by other modules (like `__init__.py` referencing things). This might mean the dependency detection is incomplete or this is a self-contained module that other parts import directly.


The module functions as a self-contained orchestration layer that generates acquisition plans, manages lane execution, and tracks mission outcomes. It handles profile normalization, budget allocation for feed dominance, and domain expansion from queries. The high symbol count indicates this is a substantial component, while its isolated dependency position suggests it operates independently from other subsystems in the runtime.
</think>

The `runtime/acquisition` module orchestrates the creation, execution, and reporting of threat intelligence acquisition plans across multiple parallel lanes, handling everything from query normalization and eligibility determination through budget enforcement and mission completion tracking. It serves as the central planning engine for nonfeed and feed-based acquisition, defining lane constants, building execution plans with concurrency and timeout parameters, and evaluating terminal states for mission-critical operations. The module enforces the feed dominance budget policy to prioritize nonfeed lanes once mandatory sources have resolved, and provides mission intent inference to adapt acquisition behavior based on query characteristics. Domain expansion logic extracts actionable indicators from threat-category keywords to bootstrap downstream lanes like CT, WAYBACK, and PASSIVE_DNS. With approximately 3,300 lines across 14 files and no external dependencies, this module functions as a self-contained orchestration hub—its changes affect the entire acquisition pipeline, making it a high-impact component of the runtime system. The codebase is unusually isolated for its complexity level, suggesting it either represents a foundational subsystem that other modules import directly or operates as a top-level entry point.

## Structure

| Language | Files |
|---|---|
| Python | 14 |

### Largest Files

- `plan_builder.py` (587 lines)
- `mission.py` (498 lines)
- `report_builder.py` (349 lines)
- `nonfeed_eligibility.py` (322 lines)
- `budget.py` (254 lines)
- `domain_expansion.py` (233 lines)
- `nonfeed_outcomes.py` (207 lines)
- `__init__.py` (200 lines)
- `threat_dictionary.py` (175 lines)
- `profile.py` (135 lines)

<details><summary><strong>Show 4 more files</strong></summary>

- `lane_plan.py` (112 lines)
- `lane_constants.py` (96 lines)
- `acquisition_lanes.py` (87 lines)
- `cid_detection.py` (66 lines)

</details>


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>AcquisitionLane</code> (Class) in lane_constants.py — referenced in 20 files</p>
<details><summary>Canonical acquisition lane identifiers.</summary>
<div class="doc-comment">
<p>Canonical acquisition lane identifiers.</p>
<p></p>
<p>NOTE: FEED and PUBLIC lanes are NOT run via run_enabled_acquisition_lanes().</p>
<p>They are run by SprintScheduler via its own pipeline calls.</p>
<p>STEALTH lane is NOT run here — caller must explicitly enable it.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, _core.py, acquisition_strategy.py, bgp_passive_dns_adapter.py, censys_lane.py +11 more</li></ul>
</li>
<li>
<p><code>build_acquisition_plan</code> (Function) in plan_builder.py — referenced in 17 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, _lazy_imports.py, _planning.py, acquisition_strategy.py, concept_domain_expander.py +9 more</li></ul>
</li>
<li>
<p><code>build_acquisition_report</code> (Function) in report_builder.py — referenced in 14 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, _planning.py, acquisition_strategy.py, live_sprint_measurement.py, sprint_entrypoint.py +6 more</li></ul>
</li>
<li>
<p><code>FeedDominanceBudget</code> (Class) in budget.py — referenced in 8 files</p>
<details><summary>F216E / Sprint C: Canonical feed dominance budget policy.</summary>
<div class="doc-comment">
<p>F216E / Sprint C: Canonical feed dominance budget policy.</p>
<p></p>
<p>Limits how many feed findings can be accepted before nonfeed lanes</p>
<p>are given priority. Activated for non-default profiles when mandatory</p>
<p>nonfeed lanes are unresolved.</p>
<p></p>
<p>F227D: Added mission_intent context to adjust cap thresholds.</p>
<p>F230D: Added nonfeed_diagnostic profile per-intent thresholds.</p>
<p></p>
<p>Migration: @dataclass(frozen=True) → msgspec.Struct().</p>
<p>Benefits: C-level __init__ (~2-3× faster), no GC tracking (~40B saved),</p>
<p>zero-cost property access on hot paths.</p>
<p></p>
<p>Invariants:</p>
<p>- max_feed_accepted_before_nonfeed_terminal &gt;= max_feed_per_source</p>
<p>- All limits are bounded (min 1, max 10000)</p>
<p>- Safe to use as frozen Struct field</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, acquisition_strategy.py, plan_builder.py, test_f26x_dataclass_migration.py, test_post_f230_guard.py</li></ul>
</li>
<li>
<p><code>_build_plan_impl</code> (Function) in plan_builder.py — referenced in 8 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, _planning.py, acquisition_strategy.py, test_acquisition_fallback.py, test_f227k_acquisition_lane_parity.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (87)</summary>
<ul>
<li><code>build_acquisition_report</code> (report_builder.py)</li>
<li><code>_build_nonfeed_lane_eligibility</code> (nonfeed_eligibility.py)</li>
<li><code>cap_feeding</code> (budget.py)</li>
<li><code>_build_plan_impl</code> (plan_builder.py)</li>
<li><code>build_snapshot</code> (mission.py)</li>
<li><code>_get_lane_outcome</code> (mission.py)</li>
<li><code>build_acquisition_plan</code> (plan_builder.py)</li>
<li><code>normalize_acquisition_profile</code> (profile.py)
<details><summary>F229: Runtime-normalize an acquisition_profile value.</summary>
<div class="doc-comment">
<p>F229: Runtime-normalize an acquisition_profile value.</p>
<p></p>
<p>Returns a dict with keys:</p>
<p>- input:       the raw input value</p>
<p>- effective:   the canonical profile name</p>
<p>- normalized:  True if input != effective</p>
<p>- reason:      human-readable explanation</p>
<p></p>
<p>Canonical profiles: "default", "nonfeed_diagnostic"</p>
<p>Benchmark aliases: "nonfeed_diagnostic180" → "nonfeed_diagnostic"</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>- Fail-safe: always returns a valid dict</p>
<p>- Deterministic: same input always same output</p>
</div>
</details>
</li>
<li><code>_expand_keyword_query</code> (domain_expansion.py)
<details><summary>P1-2: Expand generic query to extract actionable indicators.</summary>
<div class="doc-comment">
<p>P1-2: Expand generic query to extract actionable indicators.</p>
<p></p>
<p>Returns up to 10 keywords spanning threat actors, TTPs, and IOCs.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>- Bounded: max 10 keywords returned</p>
<p>- Fail-safe: returns [query] on any error</p>
</div>
</details>
</li>
<li><code>required_terminal_lanes</code> (nonfeed_eligibility.py)</li>
<li><code>terminality_report</code> (nonfeed_eligibility.py)</li>
<li><code>normalize_source_family_name</code> (plan_builder.py)
<details><summary>Normalize source family name to canonical form.</summary>
<div class="doc-comment">
<p>Normalize source family name to canonical form.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>- Deterministic: same input always same output</p>
</div>
</details>
</li>
<li><code>infer_mission_intent</code> (mission.py)
<details><summary>F217B: Infer mission intent from query string.</summary>
<div class="doc-comment">
<p>F217B: Infer mission intent from query string.</p>
<p></p>
<p>Returns one of: domain_recon | person_recon | infra_recon | cve_recon | unknown</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>- Bounded: O(len(query)) regex match</p>
<p>- Fail-safe: returns "unknown" on any error</p>
</div>
</details>
</li>
<li><code>_load_feed_budget_from_env</code> (budget.py)
<details><summary>Load FeedDominanceBudget from environment variables with safe fallback.</summary>
<div class="doc-comment">
<p>Load FeedDominanceBudget from environment variables with safe fallback.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>- All values clamped to safe bounds [1, 10000] or [0.0, 1.0]</p>
</div>
</details>
</li>
<li><code>canonicalize_source_family_outcomes</code> (plan_builder.py)
<details><summary>Canonicalize a list of source family outcomes.</summary>
<div class="doc-comment">
<p>Canonicalize a list of source family outcomes.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
</div>
</details>
</li>
<li><code>_evaluate_family_status</code> (mission.py)
<details><summary>Evaluate the status of a lane family for mission completion.</summary>
<div class="doc-comment">
<p>Evaluate the status of a lane family for mission completion.</p>
<p></p>
<p>Returns: "complete" | "terminal_no_evidence" | "skipped" | "unresolved"</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
</div>
</details>
</li>
<li><code>build_lane_query</code> (plan_builder.py)</li>
<li><code>_get_keyword_domain_expansion</code> (domain_expansion.py)
<details><summary>F1-3: Extract domain expansion seeds from keywords in query.</summary>
<div class="doc-comment">
<p>F1-3: Extract domain expansion seeds from keywords in query.</p>
<p></p>
<p>Maps threat-category keywords → expansion domains for lanes that need</p>
<p>a domain/IP seed (CT, WAYBACK, PASSIVE_DNS).</p>
<p></p>
<p>E.g. "ransomware C2" → ["ransomware_tracker.abuse.ch"]</p>
<p>"botnet"         → ["abuse.ch", "feodotracker.nl", "urlhaus.abuse.ch"]</p>
<p></p>
<p>Returns:</p>
<p>List of domain expansion strings (bounded, deduped, first-seen order).</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>- Bounded: max 10 domains returned</p>
<p>- Fail-safe: returns [] on any error</p>
</div>
</details>
</li>
<li><code>_derive_exit_reason</code> (mission.py)</li>
<li><code>run_enabled_acquisition_lanes_streaming</code> (acquisition_lanes.py)</li>
<li><code>normalize_source_family_outcome</code> (plan_builder.py)
<details><summary>Normalize a single source family outcome dict.</summary>
<div class="doc-comment">
<p>Normalize a single source family outcome dict.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
</div>
</details>
</li>
<li><code>_extract_cids_from_text</code> (cid_detection.py)
<details><summary>Extract unique explicit CIDs from arbitrary text. Bounded dedup.</summary>
<div class="doc-comment">
<p>Extract unique explicit CIDs from arbitrary text. Bounded dedup.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>- Bounded: O(n) where n = word count, max ~1000 chars</p>
<p>- Fail-safe: returns [] on any error</p>
</div>
</details>
</li>
<li><code>select_ct_domains_for_passivedns_pivot</code> (plan_builder.py)</li>
<li><code>run_enabled_acquisition_lanes</code> (acquisition_lanes.py)</li>
<li><code>reconcile_lane_detail_fields</code> (report_builder.py)</li>
<li><code>_pick_best_terminal</code> (plan_builder.py) — <span class="doc-comment-inline">Pick the best terminal state from a list of outcomes.</span></li>
<li><code>feed_budget_to_dict</code> (budget.py)
<details><summary>Convert FeedDominanceBudget to a JSON-serializable dict.</summary>
<div class="doc-comment">
<p>Convert FeedDominanceBudget to a JSON-serializable dict.</p>
<p></p>
<p>MODERNIZATION (Issue #18): Replaces 27-line _feed_budget_to_dict with</p>
<p>msgspec.to_builtins() — C-level, ~50 ns vs 5-10 µs, 100× faster.</p>
<p></p>
<p>Handles None → {} for convenience.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>- Fail-safe: returns {} for None or invalid input</p>
</div>
</details>
</li>
<li><code>_make_lane_plan</code> (plan_builder.py)</li>
<li><code>complete_source_family_outcomes_from_lane_details</code> (report_builder.py)</li>
<li><code>_mission_lanes</code> (mission.py)</li>
<li><code>_get_ner_engine</code> (domain_expansion.py)
<details><summary>Lazy import brain.ner_engine.extract_iocs_from_text with module-level cache.</summary>
<div class="doc-comment">
<p>Lazy import brain.ner_engine.extract_iocs_from_text with module-level cache.</p>
<p></p>
<p>FIRST CALL: imports and caches the function reference.</p>
<p>SUBSEQUENT CALLS: returns cached reference (no repeated import).</p>
<p></p>
<p>M1 8GB benefit: eliminates ~50 ms import cost per sprint call × 50 calls = 2.5 s.</p>
</div>
</details>
</li>
<li><code>lookup_threat_entity</code> (threat_dictionary.py)
<details><summary>O(1) dict lookup for threat entity type + canonical name.</summary>
<div class="doc-comment">
<p>O(1) dict lookup for threat entity type + canonical name.</p>
<p></p>
<p>Returns (entity_type, canonical_name) if found, None otherwise.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>- Fail-safe: returns None for unknown entities</p>
<p>- Deterministic: same input always same output</p>
</div>
</details>
</li>
<li><code>_disabled_reason</code> (lane_plan.py)
<details><summary>Compute why a lane is disabled (for reporting).</summary>
<div class="doc-comment">
<p>Compute why a lane is disabled (for reporting).</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
</div>
</details>
</li>
<li><code>values</code> (lane_constants.py)</li>
<li><code>_lane_max_items</code> (plan_builder.py) — <span class="doc-comment-inline">Return max items for lane based on UMA state.</span></li>
<li><code>__init__</code> (mission.py)</li>
<li><code>lane_is_terminal</code> (nonfeed_eligibility.py)
<details><summary>Return True if the lane outcome represents a terminal state.</summary>
<div class="doc-comment">
<p>Return True if the lane outcome represents a terminal state.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>- Fail-safe: returns False for malformed input</p>
</div>
</details>
</li>
<li><code>_has_explicit_cid</code> (cid_detection.py)
<details><summary>Return True if value is an explicit IPFS CID (CIDv0 or CIDv1 base32).</summary>
<div class="doc-comment">
<p>Return True if value is an explicit IPFS CID (CIDv0 or CIDv1 base32).</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>- Bounded: O(1) length check before regex</p>
<p>- Fail-safe: returns False for malformed input</p>
</div>
</details>
</li>
<li><code>_extract_crypto_from_query</code> (plan_builder.py) — <span class="doc-comment-inline">Extract crypto indicators from query string.</span></li>
<li><code>_base_concurrency</code> (plan_builder.py)
<details><summary>Compute base concurrency based on UMA state and swap detection.</summary>
<div class="doc-comment">
<p>Compute base concurrency based on UMA state and swap detection.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>- Bounded: returns 1-8</p>
</div>
</details>
</li>
<li><code>_lane_concurrency</code> (plan_builder.py)
<details><summary>Adjust base concurrency per lane based on UMA state.</summary>
<div class="doc-comment">
<p>Adjust base concurrency per lane based on UMA state.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>- Bounded: returns 1-32</p>
</div>
</details>
</li>
<li><code>_lane_timeout</code> (plan_builder.py) — <span class="doc-comment-inline">Return timeout in seconds for lane.</span></li>
<li><code>normalize_passive_dns_query</code> (plan_builder.py)</li>
<li><code>_plan_to_dict</code> (report_builder.py) — <span class="doc-comment-inline">Convert plan snapshot to dict.</span></li>
<li><code>_lc</code> (lane_plan.py)
<details><summary>Adjust base concurrency based on lane name and UMA state.</summary>
<div class="doc-comment">
<p>Adjust base concurrency based on lane name and UMA state.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>- Bounded: returns 1-32</p>
</div>
</details>
</li>
<li><code>normalize_terminal_state</code> (nonfeed_eligibility.py)
<details><summary>Normalize terminal state from outcome dict or object.</summary>
<div class="doc-comment">
<p>Normalize terminal state from outcome dict or object.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
</div>
</details>
</li>
<li><code>_lane_risk</code> (plan_builder.py) — <span class="doc-comment-inline">Return risk level for lane.</span></li>
<li><code>is_mission_profile</code> (profile.py)
<details><summary>Return True when the profile is any nonfeed_diagnostic variant.</summary>
<div class="doc-comment">
<p>Return True when the profile is any nonfeed_diagnostic variant.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>- Fail-safe: returns False for None</p>
</div>
</details>
</li>
<li><code>lane_skip_reason</code> (plan_builder.py)</li>
<li><code>to_dict</code> (mission.py)</li>
<li><code>_has_domain_or_ip</code> (nonfeed_eligibility.py) — <span class="doc-comment-inline">Return True if query looks like it contains a domain or IP indicator.</span></li>
<li><code>_has_crypto_wallet</code> (nonfeed_eligibility.py) — <span class="doc-comment-inline">Return True if query looks like a cryptocurrency wallet.</span></li>
<li><code>_has_threat_indicator</code> (nonfeed_eligibility.py) — <span class="doc-comment-inline">Return True if query has threat indicator keywords.</span></li>
<li><code>values</code> (profile.py)</li>
<li><code>_looks_like_domain</code> (plan_builder.py) — <span class="doc-comment-inline">Return True if value looks like a domain name.</span></li>
<li><code>is_mission_profile</code> (mission.py)
<details><summary>Return True when the profile is any nonfeed_diagnostic variant.</summary>
<div class="doc-comment">
<p>Return True when the profile is any nonfeed_diagnostic variant.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>Delegated to SSOT: runtime.acquisition.profile.is_mission_profile</p>
</div>
</details>
</li>
<li><code>_mission_target_kind</code> (mission.py) — <span class="doc-comment-inline">Map mission intent to target kind.</span></li>
<li><code>_debug_to_dict</code> (report_builder.py) — <span class="doc-comment-inline">Convert nonfeed plan debug to dict.</span></li>
<li><code>kind_counts</code> (nonfeed_outcomes.py)</li>
<li><code>is_academic_profile</code> (profile.py)
<details><summary>Return True if profile enables the ACADEMIC acquisition lane.</summary>
<div class="doc-comment">
<p>Return True if profile enables the ACADEMIC acquisition lane.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>- Fail-safe: returns False for unknown profiles</p>
</div>
</details>
</li>
<li><code>get_lane_plan</code> (plan_builder.py)</li>
<li><code>is_deep_osint_m1_profile</code> (profile.py)
<details><summary>Return True if profile is the deep_osint_m1 specialized profile.</summary>
<div class="doc-comment">
<p>Return True if profile is the deep_osint_m1 specialized profile.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
</div>
</details>
</li>
<li><code>_lane_rule</code> (lane_plan.py)
<details><summary>Build a LaneRule from a LaneSpec + condition functions.</summary>
<div class="doc-comment">
<p>Build a LaneRule from a LaneSpec + condition functions.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
</div>
</details>
</li>
<li><code>normalize_terminal_state</code> (plan_builder.py) — <span class="doc-comment-inline">Normalize terminal state from outcome.</span></li>
<li><code>_has_crypto_hash</code> (nonfeed_eligibility.py) — <span class="doc-comment-inline">Return True if query looks like a crypto hash.</span></li>
<li><code>is_sentinel</code> (budget.py) — <span class="doc-comment-inline">Return True when all caps are at sentinel (None) — feature fully disabled.</span></li>
<li><code>_mission_cap_active</code> (budget.py) — <span class="doc-comment-inline">F227D: Return True when mission-aware cap should be evaluated.</span></li>
<li><code>_int</code> (budget.py)</li>
<li><code>_float</code> (budget.py)</li>
<li><code>_looks_like_ip</code> (plan_builder.py) — <span class="doc-comment-inline">Return True if s looks like an IP address.</span></li>
<li><code>_extract_ips_from_query</code> (plan_builder.py) — <span class="doc-comment-inline">Extract IP addresses from query string.</span></li>
<li><code>_extract_domain_from_ct_finding</code> (plan_builder.py) — <span class="doc-comment-inline">Extract domain from a CT finding.</span></li>
<li><code>_has_url</code> (nonfeed_eligibility.py) — <span class="doc-comment-inline">Return True if query looks like a URL.</span></li>
<li><code>to_dict</code> (nonfeed_outcomes.py) — <span class="doc-comment-inline">Convert to JSON-serializable dict via msgspec.to_builtins (C-level ~50 ns).</span></li>
<li><code>to_dict</code> (nonfeed_outcomes.py) — <span class="doc-comment-inline">Convert to JSON-serializable dict via msgspec.to_builtins (C-level ~50 ns).</span></li>
<li><code>_get_original</code> (acquisition_lanes.py)</li>
<li><code>is_lane_enabled</code> (plan_builder.py) — <span class="doc-comment-inline">Return True if lane is enabled in the snapshot.</span></li>
<li><code>get_required_families</code> (mission.py) — <span class="doc-comment-inline">Required lane families for nonfeed_diagnostic mission.</span></li>
<li><code>get_optional_families</code> (mission.py) — <span class="doc-comment-inline">Optional lane families for nonfeed_diagnostic mission.</span></li>
<li><code>_family_to_lane</code> (mission.py) — <span class="doc-comment-inline">Map lane family string to AcquisitionLane constant.</span></li>
<li><code>_has_crypto_indicator</code> (nonfeed_eligibility.py) — <span class="doc-comment-inline">Return True if query has any crypto indicator.</span></li>
<li><code>is_active</code> (budget.py) — <span class="doc-comment-inline">Return True when any cap is configured (non-sentinel).</span></li>
<li><code>_nonfeed_profile_cap_active</code> (budget.py) — <span class="doc-comment-inline">F230D: Return True when nonfeed_diagnostic profile cap should be evaluated.</span></li>
<li><code>has_domain</code> (nonfeed_outcomes.py)</li>
<li><code>has_ip</code> (nonfeed_outcomes.py)</li>
<li><code>has_url</code> (nonfeed_outcomes.py)</li>
<li><code>is_lane_enabled</code> (nonfeed_outcomes.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (20)</summary>
<ul>
<li><code>NonfeedMissionController</code> (mission.py)
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
<li><code>FeedDominanceBudget</code> (budget.py)
<details><summary>F216E / Sprint C: Canonical feed dominance budget policy.</summary>
<div class="doc-comment">
<p>F216E / Sprint C: Canonical feed dominance budget policy.</p>
<p></p>
<p>Limits how many feed findings can be accepted before nonfeed lanes</p>
<p>are given priority. Activated for non-default profiles when mandatory</p>
<p>nonfeed lanes are unresolved.</p>
<p></p>
<p>F227D: Added mission_intent context to adjust cap thresholds.</p>
<p>F230D: Added nonfeed_diagnostic profile per-intent thresholds.</p>
<p></p>
<p>Migration: @dataclass(frozen=True) → msgspec.Struct().</p>
<p>Benefits: C-level __init__ (~2-3× faster), no GC tracking (~40B saved),</p>
<p>zero-cost property access on hot paths.</p>
<p></p>
<p>Invariants:</p>
<p>- max_feed_accepted_before_nonfeed_terminal &gt;= max_feed_per_source</p>
<p>- All limits are bounded (min 1, max 10000)</p>
<p>- Safe to use as frozen Struct field</p>
</div>
</details>
</li>
<li><code>NonfeedMissionSnapshot</code> (mission.py)
<details><summary>F217B: Snapshot of nonfeed mission state for telemetry.</summary>
<div class="doc-comment">
<p>F217B: Snapshot of nonfeed mission state for telemetry.</p>
<p></p>
<p>Kept here (vs nonfeed_outcomes.py) because it's constructed by</p>
<p>NonfeedMissionController.build_snapshot().</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
</div>
</details>
</li>
<li><code>AcquisitionLane</code> (lane_constants.py)
<details><summary>Canonical acquisition lane identifiers.</summary>
<div class="doc-comment">
<p>Canonical acquisition lane identifiers.</p>
<p></p>
<p>NOTE: FEED and PUBLIC lanes are NOT run via run_enabled_acquisition_lanes().</p>
<p>They are run by SprintScheduler via its own pipeline calls.</p>
<p>STEALTH lane is NOT run here — caller must explicitly enable it.</p>
</div>
</details>
</li>
<li><code>NonfeedSeedContext</code> (nonfeed_outcomes.py)
<details><summary>F217: Seed context for nonfeed lane seeding.</summary>
<div class="doc-comment">
<p>F217: Seed context for nonfeed lane seeding.</p>
<p></p>
<p>Provides domain/IP/URL seeds from previous lane results</p>
<p>to bootstrap CT, WAYBACK, PASSIVE_DNS lanes.</p>
</div>
</details>
</li>
<li><code>AcquisitionStrategySnapshot</code> (nonfeed_outcomes.py)
<details><summary>F206BG: Canonical snapshot of acquisition strategy plan.</summary>
<div class="doc-comment">
<p>F206BG: Canonical snapshot of acquisition strategy plan.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- Bounded: max 12 lanes in plan</p>
<p>- No network I/O, no model/MLX load</p>
</div>
</details>
</li>
<li><code>AcquisitionLaneOutcome</code> (nonfeed_outcomes.py)
<details><summary>F206BG: Canonical outcome for a single acquisition lane.</summary>
<div class="doc-comment">
<p>F206BG: Canonical outcome for a single acquisition lane.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- All fields have safe defaults</p>
<p>- to_dict() is JSON-safe</p>
</div>
</details>
</li>
<li><code>SourceFamilyOutcome</code> (nonfeed_outcomes.py)
<details><summary>F216G: Canonical outcome for a source family (aggregated across lanes).</summary>
<div class="doc-comment">
<p>F216G: Canonical outcome for a source family (aggregated across lanes).</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- All fields have safe defaults</p>
</div>
</details>
</li>
<li><code>AcquisitionProfile</code> (profile.py) — <span class="doc-comment-inline">Acquisition profile constants — mirrors original StrEnum-style class.</span></li>
<li><code>NonfeedPlanDebug</code> (nonfeed_outcomes.py)
<details><summary>F217C: Debug info for nonfeed acquisition plan.</summary>
<div class="doc-comment">
<p>F217C: Debug info for nonfeed acquisition plan.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
</div>
</details>
</li>
<li><code>AcquisitionContext</code> (lane_plan.py) — <span class="doc-comment-inline">Shared context for lane eligibility evaluation.</span></li>
<li><code>AcquisitionLanePlan</code> (nonfeed_outcomes.py)
<details><summary>F206BG: Plan for a single acquisition lane.</summary>
<div class="doc-comment">
<p>F206BG: Plan for a single acquisition lane.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- All fields have safe defaults</p>
<p>- JSON-safe (msgspec.Struct)</p>
</div>
</details>
</li>
<li><code>LaneSpec</code> (lane_plan.py)
<details><summary>Per-lane specification for acquisition planning.</summary>
<div class="doc-comment">
<p>Per-lane specification for acquisition planning.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- max_items is bounded [1, 10000]</p>
<p>- timeout_s is bounded [1, 3600]</p>
<p>- concurrency is bounded [1, 32]</p>
</div>
</details>
</li>
<li><code>LaneRule</code> (lane_plan.py) — <span class="doc-comment-inline">A single lane enable/disable rule with condition functions.</span></li>
<li><code>MissionTargetKind</code> (mission.py) — <span class="doc-comment-inline">F217B: Target kind for mission telemetry.</span></li>
<li><code>MissionIntent</code> (mission.py) — <span class="doc-comment-inline">F217B: Canonical mission intent taxonomy for nonfeed_diagnostic profile.</span></li>
<li><code>MandatoryLaneTerminality</code> (nonfeed_outcomes.py) — <span class="doc-comment-inline">F228B: Represents a mandatory lane and its terminality requirements.</span></li>
<li><code>NonfeedMissionExitReason</code> (mission.py) — <span class="doc-comment-inline">F217B: Canonical exit reasons for nonfeed mission.</span></li>
<li><code>RiskLevel</code> (lane_constants.py) — <span class="doc-comment-inline">Risk level for acquisition lanes.</span></li>
<li><code>MandatoryLaneTerminality</code> (nonfeed_eligibility.py) — <span class="doc-comment-inline">Forward-declared for type hints. Real class in nonfeed_outcomes.py.</span></li>
</ul>
</details>

<details><summary><strong>Method</strong> (24)</summary>
<ul>
<li><code>cap_feeding</code> (budget.py)</li>
<li><code>build_snapshot</code> (mission.py)</li>
<li><code>_get_lane_outcome</code> (mission.py)</li>
<li><code>_evaluate_family_status</code> (mission.py)
<details><summary>Evaluate the status of a lane family for mission completion.</summary>
<div class="doc-comment">
<p>Evaluate the status of a lane family for mission completion.</p>
<p></p>
<p>Returns: "complete" | "terminal_no_evidence" | "skipped" | "unresolved"</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
</div>
</details>
</li>
<li><code>_derive_exit_reason</code> (mission.py)</li>
<li><code>values</code> (lane_constants.py)</li>
<li><code>__init__</code> (mission.py)</li>
<li><code>to_dict</code> (mission.py)</li>
<li><code>values</code> (profile.py)</li>
<li><code>is_mission_profile</code> (mission.py)
<details><summary>Return True when the profile is any nonfeed_diagnostic variant.</summary>
<div class="doc-comment">
<p>Return True when the profile is any nonfeed_diagnostic variant.</p>
<p></p>
<p>GHOST_INVARIANTS:</p>
<p>- No network I/O, no model/MLX load</p>
<p>Delegated to SSOT: runtime.acquisition.profile.is_mission_profile</p>
</div>
</details>
</li>
<li><code>kind_counts</code> (nonfeed_outcomes.py)</li>
<li><code>is_sentinel</code> (budget.py) — <span class="doc-comment-inline">Return True when all caps are at sentinel (None) — feature fully disabled.</span></li>
<li><code>_mission_cap_active</code> (budget.py) — <span class="doc-comment-inline">F227D: Return True when mission-aware cap should be evaluated.</span></li>
<li><code>to_dict</code> (nonfeed_outcomes.py) — <span class="doc-comment-inline">Convert to JSON-serializable dict via msgspec.to_builtins (C-level ~50 ns).</span></li>
<li><code>to_dict</code> (nonfeed_outcomes.py) — <span class="doc-comment-inline">Convert to JSON-serializable dict via msgspec.to_builtins (C-level ~50 ns).</span></li>
<li><code>get_required_families</code> (mission.py) — <span class="doc-comment-inline">Required lane families for nonfeed_diagnostic mission.</span></li>
<li><code>get_optional_families</code> (mission.py) — <span class="doc-comment-inline">Optional lane families for nonfeed_diagnostic mission.</span></li>
<li><code>_family_to_lane</code> (mission.py) — <span class="doc-comment-inline">Map lane family string to AcquisitionLane constant.</span></li>
<li><code>is_active</code> (budget.py) — <span class="doc-comment-inline">Return True when any cap is configured (non-sentinel).</span></li>
<li><code>_nonfeed_profile_cap_active</code> (budget.py) — <span class="doc-comment-inline">F230D: Return True when nonfeed_diagnostic profile cap should be evaluated.</span></li>
<li><code>has_domain</code> (nonfeed_outcomes.py)</li>
<li><code>has_ip</code> (nonfeed_outcomes.py)</li>
<li><code>has_url</code> (nonfeed_outcomes.py)</li>
<li><code>is_lane_enabled</code> (nonfeed_outcomes.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (16)</summary>
<ul>
<li><code>_THREAT_DICTIONARY</code> (threat_dictionary.py)</li>
<li><code>DOMAIN_EXPANSIONS</code> (domain_expansion.py)</li>
<li><code>TERMINAL_STATES</code> (lane_constants.py)</li>
<li><code>_INFER_RE</code> (mission.py)</li>
<li><code>_NONFEED_LANE_FAMILY_MAP</code> (mission.py)</li>
<li><code>_MISSION_FEED_CAP_THRESHOLDS</code> (budget.py)</li>
<li><code>_NONFEED_PROFILE_FEED_CAP_THRESHOLDS</code> (budget.py)</li>
<li><code>NON_TERMINAL_STATES</code> (lane_constants.py)</li>
<li><code>ACQUISITION_REPORT_SCHEMA_VERSION</code> (plan_builder.py)</li>
<li><code>_TTP_PATTERN</code> (domain_expansion.py)</li>
<li><code>_NER_ENGINE</code> (domain_expansion.py)</li>
<li><code>_ACADEMIC_PROFILES</code> (profile.py)</li>
<li><code>_DEEP_OSINT_M1_PROFILES</code> (profile.py)</li>
<li><code>_MISSION_PROFILES</code> (profile.py)</li>
<li><code>_CIDV0_RE</code> (cid_detection.py)</li>
<li><code>_CIDV1_BASE32_RE</code> (cid_detection.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 14 |
| Total lines | 3321 |
| Avg lines/file | 237 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |


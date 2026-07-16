+++
title = "tools/analyze/"
weight = 35
description = "<think> The user wants me to write a module overview for `tools/analyze` based on the provided structural context. Let me analyze the key information:  **Module Purpose:** This module provides autonom..."

[extra]
tier = 2
file_count = 3
total_lines = 737
languages = "Python"
parent_path = "tools"
has_mermaid = true
+++

<think>
The user wants me to write a module overview for `tools/analyze` based on the provided structural context. Let me analyze the key information:

**Module Purpose:** This module provides autonomous query analysis for intelligent research configuration. It analyzes research queries and generates optimal research profiles that determine which intelligence tools to activate, which sources to search, privacy levels, Tor usage, ToT reasoning needs, models to load, and estimated execution time.

**Key Characteristics:**
- 3 Python files, 737 total lines (average 245 lines/file)
- No outgoing dependencies (Tier 2, self-contained)
- Only 1 incoming dependent (project_types.py)
- `__init__` function referenced in 641 files - this is an interesting metric showing it's likely used as an import point
- The main `analyze` function is referenced in 57 files

**Architecture:**
- Hub-like in terms of influence (the `__init__` being referenced in 641 files is significant)
- Leaf in terms of dependencies (no outgoing deps)
- The `AutonomousAnalyzer` class is the central abstraction
- `AutoResearchProfile` is the main output type

**Key observations:**
1. The `__init__` being referenced 641 times is unusual - this suggests it may serve as a convenience import/export point
2. The `analyze` function being referenced in 57 files shows moderate usage
3. This module is a decision-making engine that configures the research pipeline

Let me write a concise, well-structured overview that:
- Starts with what the module DOES (not "consists of")
- Explains its architectural role
- Notes the significance of the high reference count
- Avoids listing files/classes unnecessarily
- Varies sentence structure
</think>

This module serves as the intelligent query analysis engine for the research system, examining incoming research queries and producing optimized configuration profiles that dictate how subsequent intelligence operations should execute. The core `AutonomousAnalyzer` class evaluates queries to determine which tools to activate, what privacy safeguards are needed, whether tree-of-thought reasoning applies, which models to load, and how long execution will take. The module's primary output—`AutoResearchProfile`—encapsulates all these settings into a portable configuration object that downstream components consume to initialize research tasks.

Despite having no outgoing dependencies, the module's `__init__` exports are referenced by an unusually high 641 files across the codebase, suggesting this module functions as a critical re-export hub that makes the analyzer accessible throughout the project. The main `analyze` function appears in 57 files, indicating moderate direct usage for query processing. This high fan-out export pattern means the module has significant architectural influence despite its compact three-file structure.

The module handles complexity analysis with optional ToT integration, providing a fallback mechanism when that dependency is unavailable, and includes utilities for health checking, serialization, and capability discovery. With 737 lines distributed across three Python files, the module maintains a focused, well-contained implementation that concentrates complex decision logic in a single cohesive unit.

## Dependency Diagram

{% mermaid() %}
graph LR
    m_tools_analyze["<b>tools/analyze/</b>"]
    style m_tools_analyze fill:#a78bfa,color:#0d0d0d,stroke:#a78bfa
    m_project_types_py["project_types.py/"]
    m_project_types_py -->|1| m_tools_analyze
    classDef default fill:#1a1a2e,stroke:#a78bfa,color:#e0e0e0
    click m_tools_analyze "/wiki/tools-analyze/"
    click m_project_types_py "/wiki/project_types.py/"
{% end %}

## Structure

| Language | Files |
|---|---|
| Python | 3 |

### Largest Files

- `autonomous_analyzer.py` (410 lines)
- `analyze_report.py` (232 lines)
- `_analyze_deep.py` (95 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

Used by **1 files** across **1 modules**.

**[project_types.py/](@/wiki/project_types.py.md)** (1 files):
- `project_types.py`



## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>__init__</code> (Function) in autonomous_analyzer.py — referenced in 641 files</p>
<details><summary>Initialize AutonomousAnalyzer.</summary>
<div class="doc-comment">
<p>Initialize AutonomousAnalyzer.</p>
<p></p>
<p>Args:</p>
<p>tot_integration: Optional TotIntegrationLayer for complexity analysis</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, _base.py, _body_hash.py, _buffer.py +592 more</li></ul>
</li>
<li>
<p><code>analyze</code> (Function) in autonomous_analyzer.py — referenced in 57 files</p>
<details><summary>Analyze query and generate optimal research profile.</summary>
<div class="doc-comment">
<p>Analyze query and generate optimal research profile.</p>
<p></p>
<p>Args:</p>
<p>query: Research query to analyze</p>
<p></p>
<p>Returns:</p>
<p>AutoResearchProfile with optimal configuration</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: _shared.py, advanced_image_osint.py, bgp_advisor_adapter.py, blockchain_analyzer.py, ci_root_scripts_guard.py +51 more</li></ul>
</li>
<li>
<p><code>AutoResearchProfile</code> (Class) in autonomous_analyzer.py — referenced in 3 files</p>
<details><summary>Autonomous research configuration profile.</summary>
<div class="doc-comment">
<p>Autonomous research configuration profile.</p>
<p></p>
<p>Generated by AutonomousAnalyzer based on query content analysis.</p>
<p>Contains all settings needed to execute an optimized research task.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: project_types.py, shadow_inputs.py</li></ul>
</li>
<li>
<p><code>AutonomousAnalyzer</code> (Class) in autonomous_analyzer.py — referenced in 2 files</p>
<details><summary>Autonomous query analyzer for intelligent research configuration.</summary>
<div class="doc-comment">
<p>Autonomous query analyzer for intelligent research configuration.</p>
<p></p>
<p>Analyzes research queries to automatically determine:</p>
<p>- Which intelligence tools to activate</p>
<p>- Which sources to search</p>
<p>- Required privacy level and Tor usage</p>
<p>- Whether ToT reasoning is needed</p>
<p>- Which models to load</p>
<p>- Estimated execution time</p>
<p></p>
<p>Usage:</p>
<p>&gt;&gt;&gt; analyzer = AutonomousAnalyzer()</p>
<p>&gt;&gt;&gt; profile = analyzer.analyze("Find information about Bitcoin wallet addresses on dark web")</p>
<p>&gt;&gt;&gt; print(profile.tools)  # {'stealth_crawler', 'blockchain_analyzer'}</p>
<p>&gt;&gt;&gt; print(profile.privacy_level)  # 'MAXIMUM'</p>
<p>&gt;&gt;&gt; print(profile.use_tor)  # True</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: project_types.py</li></ul>
</li>
<li>
<p><code>_estimate_depth</code> (Function) in autonomous_analyzer.py — referenced in 2 files</p>
<details><summary>Estimate research depth based on tools and complexity.</summary>
<div class="doc-comment">
<p>Estimate research depth based on tools and complexity.</p>
<p></p>
<p>Args:</p>
<p>tool_count: Number of tools selected</p>
<p>complexity: Complexity score</p>
<p></p>
<p>Returns:</p>
<p>Depth level string</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: tot_integration.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (19)</summary>
<ul>
<li><code>_fallback_complexity_analysis</code> (autonomous_analyzer.py)
<details><summary>Fallback complexity analysis without ToT integration.</summary>
<div class="doc-comment">
<p>Fallback complexity analysis without ToT integration.</p>
<p></p>
<p>Args:</p>
<p>query: Research query</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (complexity_score, use_tot)</p>
</div>
</details>
</li>
<li><code>_generate_reasoning</code> (autonomous_analyzer.py)
<details><summary>Generate human-readable reasoning for the profile.</summary>
<div class="doc-comment">
<p>Generate human-readable reasoning for the profile.</p>
<p></p>
<p>Args:</p>
<p>profile: AutoResearchProfile</p>
<p></p>
<p>Returns:</p>
<p>Reasoning string</p>
</div>
</details>
</li>
<li><code>analyze</code> (autonomous_analyzer.py)
<details><summary>Analyze query and generate optimal research profile.</summary>
<div class="doc-comment">
<p>Analyze query and generate optimal research profile.</p>
<p></p>
<p>Args:</p>
<p>query: Research query to analyze</p>
<p></p>
<p>Returns:</p>
<p>AutoResearchProfile with optimal configuration</p>
</div>
</details>
</li>
<li><code>_infer_sources</code> (autonomous_analyzer.py)
<details><summary>Infer which sources to search based on selected tools.</summary>
<div class="doc-comment">
<p>Infer which sources to search based on selected tools.</p>
<p></p>
<p>Args:</p>
<p>tools: Detected tools</p>
<p>query: Original query</p>
<p></p>
<p>Returns:</p>
<p>Set of source types</p>
</div>
</details>
</li>
<li><code>_detect_tools</code> (autonomous_analyzer.py)
<details><summary>Detect which intelligence tools should be activated.</summary>
<div class="doc-comment">
<p>Detect which intelligence tools should be activated.</p>
<p></p>
<p>Args:</p>
<p>query: Research query</p>
<p></p>
<p>Returns:</p>
<p>Set of tool names to activate</p>
</div>
</details>
</li>
<li><code>_determine_privacy</code> (autonomous_analyzer.py)
<details><summary>Determine privacy level and Tor activation.</summary>
<div class="doc-comment">
<p>Determine privacy level and Tor activation.</p>
<p></p>
<p>Args:</p>
<p>query: Research query</p>
<p>tools: Detected tools</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (privacy_level, use_tor)</p>
</div>
</details>
</li>
<li><code>_estimate_depth</code> (autonomous_analyzer.py)
<details><summary>Estimate research depth based on tools and complexity.</summary>
<div class="doc-comment">
<p>Estimate research depth based on tools and complexity.</p>
<p></p>
<p>Args:</p>
<p>tool_count: Number of tools selected</p>
<p>complexity: Complexity score</p>
<p></p>
<p>Returns:</p>
<p>Depth level string</p>
</div>
</details>
</li>
<li><code>_analyze_complexity</code> (autonomous_analyzer.py)
<details><summary>Analyze query complexity and determine ToT activation.</summary>
<div class="doc-comment">
<p>Analyze query complexity and determine ToT activation.</p>
<p></p>
<p>Args:</p>
<p>query: Research query</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (complexity_score, use_tot)</p>
</div>
</details>
</li>
<li><code>_determine_models</code> (autonomous_analyzer.py)
<details><summary>Determine which models are needed based on profile.</summary>
<div class="doc-comment">
<p>Determine which models are needed based on profile.</p>
<p></p>
<p>Args:</p>
<p>profile: AutoResearchProfile</p>
<p></p>
<p>Returns:</p>
<p>Set of model names</p>
</div>
</details>
</li>
<li><code>_estimate_time</code> (autonomous_analyzer.py)
<details><summary>Estimate execution time based on profile.</summary>
<div class="doc-comment">
<p>Estimate execution time based on profile.</p>
<p></p>
<p>Args:</p>
<p>profile: AutoResearchProfile</p>
<p></p>
<p>Returns:</p>
<p>Estimated time in seconds</p>
</div>
</details>
</li>
<li><code>_determine_tot_mode</code> (autonomous_analyzer.py)
<details><summary>Determine ToT mode based on complexity.</summary>
<div class="doc-comment">
<p>Determine ToT mode based on complexity.</p>
<p></p>
<p>Args:</p>
<p>complexity: Complexity score</p>
<p></p>
<p>Returns:</p>
<p>ToT mode string</p>
</div>
</details>
</li>
<li><code>__init__</code> (autonomous_analyzer.py)
<details><summary>Initialize AutonomousAnalyzer.</summary>
<div class="doc-comment">
<p>Initialize AutonomousAnalyzer.</p>
<p></p>
<p>Args:</p>
<p>tot_integration: Optional TotIntegrationLayer for complexity analysis</p>
</div>
</details>
</li>
<li><code>create_autonomous_analyzer</code> (autonomous_analyzer.py)
<details><summary>Create AutonomousAnalyzer with optional ToT integration.</summary>
<div class="doc-comment">
<p>Create AutonomousAnalyzer with optional ToT integration.</p>
<p></p>
<p>Args:</p>
<p>tot_integration: Optional TotIntegrationLayer</p>
<p></p>
<p>Returns:</p>
<p>Configured AutonomousAnalyzer</p>
</div>
</details>
</li>
<li><code>_compile_patterns</code> (autonomous_analyzer.py) — <span class="doc-comment-inline">Compile regex patterns for efficient matching.</span></li>
<li><code>health_check</code> (autonomous_analyzer.py) — <span class="doc-comment-inline">Check if analyzer is operational.</span></li>
<li><code>section</code> (analyze_report.py)</li>
<li><code>to_dict</code> (autonomous_analyzer.py) — <span class="doc-comment-inline">Convert profile to dictionary for serialization.</span></li>
<li><code>from_dict</code> (autonomous_analyzer.py) — <span class="doc-comment-inline">Create profile from dictionary.</span></li>
<li><code>get_capabilities</code> (autonomous_analyzer.py) — <span class="doc-comment-inline">Get analyzer capabilities.</span></li>
</ul>
</details>

<details><summary><strong>Class</strong> (2)</summary>
<ul>
<li><code>AutonomousAnalyzer</code> (autonomous_analyzer.py)
<details><summary>Autonomous query analyzer for intelligent research configuration.</summary>
<div class="doc-comment">
<p>Autonomous query analyzer for intelligent research configuration.</p>
<p></p>
<p>Analyzes research queries to automatically determine:</p>
<p>- Which intelligence tools to activate</p>
<p>- Which sources to search</p>
<p>- Required privacy level and Tor usage</p>
<p>- Whether ToT reasoning is needed</p>
<p>- Which models to load</p>
<p>- Estimated execution time</p>
<p></p>
<p>Usage:</p>
<p>&gt;&gt;&gt; analyzer = AutonomousAnalyzer()</p>
<p>&gt;&gt;&gt; profile = analyzer.analyze("Find information about Bitcoin wallet addresses on dark web")</p>
<p>&gt;&gt;&gt; print(profile.tools)  # {'stealth_crawler', 'blockchain_analyzer'}</p>
<p>&gt;&gt;&gt; print(profile.privacy_level)  # 'MAXIMUM'</p>
<p>&gt;&gt;&gt; print(profile.use_tor)  # True</p>
</div>
</details>
</li>
<li><code>AutoResearchProfile</code> (autonomous_analyzer.py)
<details><summary>Autonomous research configuration profile.</summary>
<div class="doc-comment">
<p>Autonomous research configuration profile.</p>
<p></p>
<p>Generated by AutonomousAnalyzer based on query content analysis.</p>
<p>Contains all settings needed to execute an optimized research task.</p>
</div>
</details>
</li>
</ul>
</details>

<details><summary><strong>Method</strong> (17)</summary>
<ul>
<li><code>_fallback_complexity_analysis</code> (autonomous_analyzer.py)
<details><summary>Fallback complexity analysis without ToT integration.</summary>
<div class="doc-comment">
<p>Fallback complexity analysis without ToT integration.</p>
<p></p>
<p>Args:</p>
<p>query: Research query</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (complexity_score, use_tot)</p>
</div>
</details>
</li>
<li><code>_generate_reasoning</code> (autonomous_analyzer.py)
<details><summary>Generate human-readable reasoning for the profile.</summary>
<div class="doc-comment">
<p>Generate human-readable reasoning for the profile.</p>
<p></p>
<p>Args:</p>
<p>profile: AutoResearchProfile</p>
<p></p>
<p>Returns:</p>
<p>Reasoning string</p>
</div>
</details>
</li>
<li><code>analyze</code> (autonomous_analyzer.py)
<details><summary>Analyze query and generate optimal research profile.</summary>
<div class="doc-comment">
<p>Analyze query and generate optimal research profile.</p>
<p></p>
<p>Args:</p>
<p>query: Research query to analyze</p>
<p></p>
<p>Returns:</p>
<p>AutoResearchProfile with optimal configuration</p>
</div>
</details>
</li>
<li><code>_infer_sources</code> (autonomous_analyzer.py)
<details><summary>Infer which sources to search based on selected tools.</summary>
<div class="doc-comment">
<p>Infer which sources to search based on selected tools.</p>
<p></p>
<p>Args:</p>
<p>tools: Detected tools</p>
<p>query: Original query</p>
<p></p>
<p>Returns:</p>
<p>Set of source types</p>
</div>
</details>
</li>
<li><code>_detect_tools</code> (autonomous_analyzer.py)
<details><summary>Detect which intelligence tools should be activated.</summary>
<div class="doc-comment">
<p>Detect which intelligence tools should be activated.</p>
<p></p>
<p>Args:</p>
<p>query: Research query</p>
<p></p>
<p>Returns:</p>
<p>Set of tool names to activate</p>
</div>
</details>
</li>
<li><code>_determine_privacy</code> (autonomous_analyzer.py)
<details><summary>Determine privacy level and Tor activation.</summary>
<div class="doc-comment">
<p>Determine privacy level and Tor activation.</p>
<p></p>
<p>Args:</p>
<p>query: Research query</p>
<p>tools: Detected tools</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (privacy_level, use_tor)</p>
</div>
</details>
</li>
<li><code>_estimate_depth</code> (autonomous_analyzer.py)
<details><summary>Estimate research depth based on tools and complexity.</summary>
<div class="doc-comment">
<p>Estimate research depth based on tools and complexity.</p>
<p></p>
<p>Args:</p>
<p>tool_count: Number of tools selected</p>
<p>complexity: Complexity score</p>
<p></p>
<p>Returns:</p>
<p>Depth level string</p>
</div>
</details>
</li>
<li><code>_analyze_complexity</code> (autonomous_analyzer.py)
<details><summary>Analyze query complexity and determine ToT activation.</summary>
<div class="doc-comment">
<p>Analyze query complexity and determine ToT activation.</p>
<p></p>
<p>Args:</p>
<p>query: Research query</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (complexity_score, use_tot)</p>
</div>
</details>
</li>
<li><code>_determine_models</code> (autonomous_analyzer.py)
<details><summary>Determine which models are needed based on profile.</summary>
<div class="doc-comment">
<p>Determine which models are needed based on profile.</p>
<p></p>
<p>Args:</p>
<p>profile: AutoResearchProfile</p>
<p></p>
<p>Returns:</p>
<p>Set of model names</p>
</div>
</details>
</li>
<li><code>_estimate_time</code> (autonomous_analyzer.py)
<details><summary>Estimate execution time based on profile.</summary>
<div class="doc-comment">
<p>Estimate execution time based on profile.</p>
<p></p>
<p>Args:</p>
<p>profile: AutoResearchProfile</p>
<p></p>
<p>Returns:</p>
<p>Estimated time in seconds</p>
</div>
</details>
</li>
<li><code>_determine_tot_mode</code> (autonomous_analyzer.py)
<details><summary>Determine ToT mode based on complexity.</summary>
<div class="doc-comment">
<p>Determine ToT mode based on complexity.</p>
<p></p>
<p>Args:</p>
<p>complexity: Complexity score</p>
<p></p>
<p>Returns:</p>
<p>ToT mode string</p>
</div>
</details>
</li>
<li><code>__init__</code> (autonomous_analyzer.py)
<details><summary>Initialize AutonomousAnalyzer.</summary>
<div class="doc-comment">
<p>Initialize AutonomousAnalyzer.</p>
<p></p>
<p>Args:</p>
<p>tot_integration: Optional TotIntegrationLayer for complexity analysis</p>
</div>
</details>
</li>
<li><code>_compile_patterns</code> (autonomous_analyzer.py) — <span class="doc-comment-inline">Compile regex patterns for efficient matching.</span></li>
<li><code>health_check</code> (autonomous_analyzer.py) — <span class="doc-comment-inline">Check if analyzer is operational.</span></li>
<li><code>to_dict</code> (autonomous_analyzer.py) — <span class="doc-comment-inline">Convert profile to dictionary for serialization.</span></li>
<li><code>from_dict</code> (autonomous_analyzer.py) — <span class="doc-comment-inline">Create profile from dictionary.</span></li>
<li><code>get_capabilities</code> (autonomous_analyzer.py) — <span class="doc-comment-inline">Get analyzer capabilities.</span></li>
</ul>
</details>

<details><summary><strong>Constant</strong> (1)</summary>
<ul>
<li><code>REPORT</code> (analyze_report.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 3 |
| Total lines | 737 |
| Avg lines/file | 245 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 1 |
| Tier | 2 |


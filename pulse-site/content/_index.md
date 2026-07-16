+++
title = "Universal Documentation"
sort_by = "weight"
+++

# Universal Documentation

<think>Let me analyze the structural context and create a technical project overview following the strict rules.

Key observations:
1. The software is primarily Python (1472 files) with Rust extensions (78 files)
2. Module names suggest an AI/machine learning system:
 - brain/: likely core AI/ML logic
 - intelligence/: cognitive capabilities
 - knowledge/: knowledge representation
 - intel/: intelligence gathering/analysis
 - recon/: reconnaissance/scanning
 - runtime/: execution environment
 - rust_extensions/: performance-critical components

3. Architecture:
 - 10 Tier 1 modules, 30 Tier 2 modules
 - 623 dependency edges
 - 8 dependency hotspots (5+ dependents)
 - 18 files in circular dependencies (some technical debt)

4. Scale:
 - 1550 total files
 - 548,329 total lines
 - Most files in brain/ and intel/ but runtime/ has most lines

Let me write a precise, factual overview without speculation:
</think>


The system operates as an intelligent code analysis and runtime execution platform, combining Python-based cognitive modules with Rust extensions for performance-critical operations. Core capabilities appear centered on code intelligence, knowledge representation, and runtime environment management, with the brain/ module (60 files, 28,368 lines) providing central AI processing and the rust_extensions/ module (84 files, 32,347 lines) delivering native performance optimizations. The intelligence/ module (65 files, 36,448 lines) handles cognitive functions while knowledge/ (63 files, 33,861 lines) manages information storage and retrieval.

The architecture splits responsibilities across distinct domains: brain/ serves as the central processing hub for AI operations, supported by intelligence/ for higher-level cognition and knowledge/ for persistent information. The runtime/ module (136 files, 64,461 lines) represents the largest single component, managing execution contexts and system operations. Communication flows from reconnaissance inputs in recon/ (81 files, 39,621 lines) through analysis pipelines in intel/ (61 files, 447 lines) into core processing in brain/, with output consumed by runtime/ and tools/ (119 files, 36,873 lines). The utils/ module (130 files, 35,916 lines) provides shared utilities across all tiers.

The codebase scales to 1,550 files across 548,329 lines, predominantly Python (1,472 files) with strategic Rust components (78 files). Ten Tier 1 modules coordinate thirty Tier 2 modules through 623 dependency edges. Eight dependency hotspots with five or more dependents indicate concentrated coupling points, while 18 files participate in circular dependencies suggesting architectural refinement opportunities. The tests/ module (187 files, 53,775 lines) provides substantial coverage, and the distribution between smaller files in intel/ (447 lines across 61 files) and larger concentrated modules like runtime/ (64,461 lines) reflects a pattern of specialized, dense implementation versus broad interface definitions.

<div class="stats-row">
<div class="stat-card"><div class="stat-value">1550</div><div class="stat-label">Files</div></div>
<div class="stat-card"><div class="stat-value">548329</div><div class="stat-label">Lines</div></div>
<div class="stat-card"><div class="stat-value">40</div><div class="stat-label">Modules</div></div>
<div class="stat-card"><div class="stat-value">2</div><div class="stat-label">Languages</div></div>
<div class="stat-card"><div class="stat-value">1</div><div class="stat-label">Contributors</div></div>
</div>

### Language Distribution

| Language | Files |
|---|---|
| Python | 1472 |
| Rust | 78 |

## Explore

<div class="quick-links">
<a href="/onboard/" class="quick-link"><strong>Onboard</strong><br>Getting started guide</a>
<a href="/wiki/" class="quick-link"><strong>Reference</strong><br>Per-module documentation</a>
<a href="/glossary/" class="quick-link"><strong>Glossary</strong><br>Domain concepts &amp; vocabulary</a>
<a href="/changelog/" class="quick-link"><strong>Changelog</strong><br>Recent changes</a>
<a href="/timeline/" class="quick-link"><strong>Timeline</strong><br>Development activity</a>
<a href="/map/" class="quick-link"><strong>Map</strong><br>Dependency graph</a>
<a href="/explorer/" class="quick-link"><strong>Explorer</strong><br>Visual treemap</a>
</div>

## Recent Activity

Week of 2026-07-13: **11** commits across **717** files by **1** contributors.

Most active files: `runtime/sprint_scheduler.py`, `knowledge/duckdb_store.py`, `core/__main__.py`, `pipeline/live_public_pipeline.py`, `fetching/public_fetcher.py`

## Core Modules

<div class="module-grid">

<div class="module-card tier-1-card">
<h3><a href="/wiki/brain/">brain/</a></h3>
<div class="module-stats">60 files · 28368 lines</div>
<p><think>
The user wants me to create a module overview for the "brain" module based on the provided structural context. Let me analyze what this module...</p>
<ul class="sub-modules">
<li><a href="/wiki/brain-hypothesis_engine/">brain/hypothesis_engine/</a> <span class="sub-stats">(6 files)</span></li>
</ul>
</div>

<div class="module-card tier-1-card">
<h3><a href="/wiki/intel/">intel/</a></h3>
<div class="module-stats">61 files · 447 lines</div>
<p><think>Let me analyze the structural context for the "intel" module:

1. **What it does**: This is a large intelligence gathering module with 61 Pytho...</p>
</div>

<div class="module-card tier-1-card">
<h3><a href="/wiki/intelligence/">intelligence/</a></h3>
<div class="module-stats">65 files · 36448 lines</div>
<p><think>
Let me analyze the structural context provided for the "intelligence" module:

1. **Module purpose**: Looking at the file names, this module a...</p>
</div>

<div class="module-card tier-1-card">
<h3><a href="/wiki/knowledge/">knowledge/</a></h3>
<div class="module-stats">63 files · 33861 lines</div>
<p><think>
The user wants me to create a module overview for the "knowledge" module based on the provided structural context. Let me analyze the data:

*...</p>
<ul class="sub-modules">
<li><a href="/wiki/knowledge-explainer/">knowledge/explainer/</a> <span class="sub-stats">(3 files)</span></li>
<li><a href="/wiki/knowledge-graph/">knowledge/graph/</a> <span class="sub-stats">(3 files)</span></li>
<li><a href="/wiki/knowledge-sprint_facts/">knowledge/sprint_facts/</a> <span class="sub-stats">(5 files)</span></li>
</ul>
</div>

<div class="module-card tier-1-card">
<h3><a href="/wiki/recon/">recon/</a></h3>
<div class="module-stats">81 files · 39621 lines</div>
<p><think>
The user wants me to create a module overview for the "recon" module based on the structural context provided. Let me analyze what this module...</p>
<ul class="sub-modules">
<li><a href="/wiki/recon-dns/">recon/dns/</a> <span class="sub-stats">(3 files)</span></li>
<li><a href="/wiki/recon-network/">recon/network/</a> <span class="sub-stats">(3 files)</span></li>
<li><a href="/wiki/recon-protocols/">recon/protocols/</a> <span class="sub-stats">(3 files)</span></li>
<li><a href="/wiki/recon-stealth/">recon/stealth/</a> <span class="sub-stats">(4 files)</span></li>
</ul>
</div>

<div class="module-card tier-1-card">
<h3><a href="/wiki/runtime/">runtime/</a></h3>
<div class="module-stats">136 files · 64461 lines</div>
<p><think>
The user wants me to create a module overview for the "runtime" module for a codebase wiki. I need to follow strict rules:

1. NEVER start wit...</p>
<ul class="sub-modules">
<li><a href="/wiki/runtime-acquisition/">runtime/acquisition/</a> <span class="sub-stats">(14 files)</span></li>
<li><a href="/wiki/runtime-acquisition_lanes/">runtime/acquisition_lanes/</a> <span class="sub-stats">(4 files)</span></li>
<li><a href="/wiki/runtime-adapters/">runtime/adapters/</a> <span class="sub-stats">(4 files)</span></li>
<li><a href="/wiki/runtime-protocols/">runtime/protocols/</a> <span class="sub-stats">(16 files)</span></li>
<li><a href="/wiki/runtime-scheduler/">runtime/scheduler/</a> <span class="sub-stats">(5 files)</span></li>
<li><a href="/wiki/runtime-scheduler-core/">runtime/scheduler/core/</a> <span class="sub-stats">(3 files)</span></li>
<li><a href="/wiki/runtime-scheduler_v2/">runtime/scheduler_v2/</a> <span class="sub-stats">(6 files)</span></li>
<li><a href="/wiki/runtime-sidecars/">runtime/sidecars/</a> <span class="sub-stats">(15 files)</span></li>
<li><a href="/wiki/runtime-sidecars-discovery/">runtime/sidecars/discovery/</a> <span class="sub-stats">(6 files)</span></li>
<li><a href="/wiki/runtime-sidecars-enrichment/">runtime/sidecars/enrichment/</a> <span class="sub-stats">(4 files)</span></li>
<li><a href="/wiki/runtime-sidecars-forensics/">runtime/sidecars/forensics/</a> <span class="sub-stats">(3 files)</span></li>
</ul>
</div>

<div class="module-card tier-1-card">
<h3><a href="/wiki/rust_extensions/">rust_extensions/</a></h3>
<div class="module-stats">84 files · 32347 lines</div>
<p><think>
Let me analyze this module carefully based on the structural context provided.

The module is `rust_extensions` - a Rust library that provides...</p>
<ul class="sub-modules">
<li><a href="/wiki/rust_extensions-src/">rust_extensions/src/</a> <span class="sub-stats">(77 files)</span></li>
<li><a href="/wiki/rust_extensions-src-collections/">rust_extensions/src/collections/</a> <span class="sub-stats">(3 files)</span></li>
<li><a href="/wiki/rust_extensions-src-data/">rust_extensions/src/data/</a> <span class="sub-stats">(3 files)</span></li>
<li><a href="/wiki/rust_extensions-src-hnsw/">rust_extensions/src/hnsw/</a> <span class="sub-stats">(3 files)</span></li>
</ul>
</div>

<div class="module-card tier-1-card">
<h3><a href="/wiki/tests/">tests/</a></h3>
<div class="module-stats">187 files · 53775 lines</div>
<p><think>
The user wants me to write a module overview for the `tests` module in a codebase wiki. I need to follow these critical rules:

1. NEVER start...</p>
<ul class="sub-modules">
<li><a href="/wiki/tests-manual/">tests/manual/</a> <span class="sub-stats">(6 files)</span></li>
<li><a href="/wiki/tests-utils/">tests/utils/</a> <span class="sub-stats">(3 files)</span></li>
</ul>
</div>

<div class="module-card tier-1-card">
<h3><a href="/wiki/tools/">tools/</a></h3>
<div class="module-stats">119 files · 36873 lines</div>
<p><think>
The user wants me to create a module overview for the `tools` module based on the structural context provided. Let me analyze what this module...</p>
<ul class="sub-modules">
<li><a href="/wiki/tools-analyze/">tools/analyze/</a> <span class="sub-stats">(3 files)</span></li>
</ul>
</div>

<div class="module-card tier-1-card">
<h3><a href="/wiki/utils/">utils/</a></h3>
<div class="module-stats">130 files · 35916 lines</div>
<p><think>
Let me analyze the STRUCTURAL CONTEXT for the `utils` module and create a module overview.

Key observations from the context:

1. **Module Pu...</p>
<ul class="sub-modules">
<li><a href="/wiki/utils-coreml/">utils/coreml/</a> <span class="sub-stats">(5 files)</span></li>
<li><a href="/wiki/utils-mlx_memory/">utils/mlx_memory/</a> <span class="sub-stats">(6 files)</span></li>
<li><a href="/wiki/utils-patterns/">utils/patterns/</a> <span class="sub-stats">(3 files)</span></li>
<li><a href="/wiki/utils-text/">utils/text/</a> <span class="sub-stats">(4 files)</span></li>
</ul>
</div>

</div>


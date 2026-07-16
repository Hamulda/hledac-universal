+++
title = "utils/patterns/"
weight = 39
description = "<think> Let me analyze the structural context for the `utils/patterns` module:  1. **Purpose**: This module handles pattern matching for text analysis, specifically for identifying indicators of compr..."

[extra]
tier = 2
file_count = 3
total_lines = 1225
languages = "Python"
parent_path = "utils"
has_mermaid = true
+++

<think>
Let me analyze the structural context for the `utils/patterns` module:

1. **Purpose**: This module handles pattern matching for text analysis, specifically for identifying indicators of compromise (IOCs), structured entities like CVEs, GHSA, cryptocurrency addresses, and other security-relevant patterns. It provides text matching capabilities using Aho-Corasick automaton (with Rust backend) and regex post-processing.

2. **Key responsibilities**:
 - Pattern matching text against configured patterns
 - Extracting high-precision structured entities (CVE, GHSA, SHA256, MD5, ETH addresses, etc.)
 - Providing a singleton Pattern Matcher state
 - Bootstrap patterns for OSINT literal pack
 - Feed pipeline wrapper (potentially Rust-based)

3. **Architecture role**: This is a utility module with no outgoing dependencies but used by 1 module (utils). However, the `match_text` function is referenced in 15 files across the codebase, suggesting it's a leaf module in terms of dependencies but has high fan-out (many modules depend on it). Wait, let me re-read - it says "Used by 1 files across 1 modules" in terms of direct module dependencies, but the symbols are referenced in many files. So it's a foundational utility module.

4. **Scale**: 3 files, 1225 lines total (average 408 lines/file). This is larger than the typical module average.

5. **Key symbols**: 
 - `match_text` - the primary entry point
 - `PatternHit` - core result type
 - `configure_default_bootstrap_patterns_if_empty` - initialization
 - `extract_high_precision_entities` - regex extraction

6. **Fan-in**: Despite only 1 direct dependent module, the symbols are referenced across many files (15 for match_text), indicating high usage.

Let me write the overview following the rules:
- First sentence must state what the module DOES or its PURPOSE


- Focus on purpose, responsibilities, architectural role
- Explain how it fits into the larger system
- Note complexity (1225 lines across 3 files)
- No enumeration of files/classes
- 4-8 sentences
- Vary sentence structure
- Don't start with "The X module consists of..."

I recognize this module as a critical text analysis utility that provides sophisticated pattern matching capabilities for security and intelligence work. Its core function is extracting meaningful, structured information from unstructured text, leveraging advanced algorithmic techniques to identify and categorize sensitive indicators efficiently.

The module operates as a foundational infrastructure component, offering a flexible pattern matching framework that supports both rapid text scanning and precise entity extraction. By integrating high-performance matching algorithms like the Aho-Corasick automaton with targeted regex post-processing, it enables comprehensive intelligence gathering across diverse data sources.

Its design emphasizes modularity and extensibility, allowing seamless integration with existing system architectures while maintaining high computational efficiency. The module serves as a critical bridge between raw text data and actionable intelligence insights.

Key architectural strengths include a memory-conscious approach to pattern matching, with built-in safeguards preventing excessive resource consumption. Its singleton-based design ensures consistent state management across complex processing pipelines.

The module strategically supports multiple pattern types - from cryptographic identifiers to network-specific references - enabling robust, multi-dimensional text analysis with minimal overhead.
</think>

This module provides pattern-matching and entity-extraction capabilities for text analysis, serving as the core text-scanning engine that identifies indicators of compromise and structured security-relevant data across the system. It combines Aho-Corasick automaton matching (with a Rust backend for performance) against a configurable pattern registry, followed by regex post-processing to extract high-precision entities such as CVE identifiers, cryptographic hashes, and blockchain addresses. The module maintains a singleton pattern-matcher state that defers initialization until first use, supports idempotent bootstrapping from an OSINT literal pack, and enforces a memory bound of 1000 entries per extraction call to remain safe on constrained hardware. As a dependency-free utility at Tier 2, it exposes a focused public API centered on `match_text` and `extract_high_precision_entities`, with the result type `PatternHit` encoding interned strings and byte offsets for efficient downstream processing. Despite having only one direct dependent module, its symbols are referenced across numerous files in the codebase, meaning modifications to the matching logic or entity definitions carry a wide blast radius. The module spans roughly 1,225 lines across three files, which is notably larger than typical codebase modules, reflecting the complexity of supporting a comprehensive pattern library and multiple regex definitions for diverse entity types.

## Dependency Diagram

{% mermaid() %}
graph LR
    m_utils_patterns["<b>utils/patterns/</b>"]
    style m_utils_patterns fill:#a78bfa,color:#0d0d0d,stroke:#a78bfa
    m_utils["utils/"]
    m_utils -->|1| m_utils_patterns
    classDef default fill:#1a1a2e,stroke:#a78bfa,color:#e0e0e0
    click m_utils_patterns "/wiki/utils-patterns/"
    click m_utils "/wiki/utils/"
{% end %}

## Structure

| Language | Files |
|---|---|
| Python | 3 |

### Largest Files

- `pattern_matcher.py` (1111 lines)
- `feed_pipeline_wrapper.py` (110 lines)
- `__init__.py` (4 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

Used by **1 files** across **1 modules**.

**[utils/](@/wiki/utils.md)** (1 files):
- `__init__.py`



## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>match_text</code> (Function) in pattern_matcher.py — referenced in 15 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, _enrich_stage.py, _match_stage.py, bench_f214_python314_runtime.py, benchmark_sprint_probe.py +8 more</li></ul>
</li>
<li>
<p><code>PatternHit</code> (Class) in pattern_matcher.py — referenced in 13 files</p>
<details><summary>Single pattern match result.</summary>
<div class="doc-comment">
<p>Single pattern match result.</p>
<p></p>
<p>Invariants:</p>
<p>- pattern, label are sys.intern()'d (dedup + fast compare)</p>
<p>- value is a direct substring slice from input text (NOT interned)</p>
<p>- start/end are byte offsets matching value extraction</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, _enrich_stage.py, _match_stage.py, benchmark_sprint_probe.py, live_feed_pipeline.py +6 more</li></ul>
</li>
<li>
<p><code>configure_default_bootstrap_patterns_if_empty</code> (Function) in pattern_matcher.py — referenced in 7 files</p>
<details><summary>Bootstrap the matcher with OSINT literal pack if registry is empty.</summary>
<div class="doc-comment">
<p>Bootstrap the matcher with OSINT literal pack if registry is empty.</p>
<p></p>
<p>Idempotent: does nothing when registry already contains patterns.</p>
<p>Does not overwrite existing registry.</p>
<p></p>
<p>Returns:</p>
<p>True if bootstrap was applied, False if registry was non-empty</p>
<p>or bootstrap failed.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, benchmark_sprint_probe.py, e2e_curl_cffi_protected_fixture.py, e2e_signal_fixture.py +1 more</li></ul>
</li>
<li>
<p><code>extract_high_precision_entities</code> (Function) in pattern_matcher.py — referenced in 4 files</p>
<details><summary>Extract high-precision structured entities via regex.</summary>
<div class="doc-comment">
<p>Extract high-precision structured entities via regex.</p>
<p></p>
<p>Covers: CVE, GHSA, onion v3, SHA256, MD5, SHA1, ETH.</p>
<p>Returns ExtractedEntity list sorted by start offset.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, profile_f214_runtime_workloads.py</li></ul>
</li>
<li>
<p><code>configure_patterns</code> (Function) in pattern_matcher.py — referenced in 3 files</p>
<details><summary>Update the active pattern registry.</summary>
<div class="doc-comment">
<p>Update the active pattern registry.</p>
<p></p>
<p>Args:</p>
<p>registry: Tuple of (pattern, label) pairs.</p>
<p>Pass _SEED_REGISTRY for test seeding.</p>
<p>Pass () to clear all patterns.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, test_py314_fallback.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (22)</summary>
<ul>
<li><code>match_text</code> (pattern_matcher.py)</li>
<li><code>match_text_batch</code> (pattern_matcher.py)</li>
<li><code>extract_high_precision_entities</code> (pattern_matcher.py)
<details><summary>Extract high-precision structured entities via regex.</summary>
<div class="doc-comment">
<p>Extract high-precision structured entities via regex.</p>
<p></p>
<p>Covers: CVE, GHSA, onion v3, SHA256, MD5, SHA1, ETH.</p>
<p>Returns ExtractedEntity list sorted by start offset.</p>
</div>
</details>
</li>
<li><code>feed_entry_pipeline_fast</code> (feed_pipeline_wrapper.py)</li>
<li><code>extract_structured_entities</code> (pattern_matcher.py)
<details><summary>Extract IOCs and return as structured list of dicts for GraphManager.</summary>
<div class="doc-comment">
<p>Extract IOCs and return as structured list of dicts for GraphManager.</p>
<p></p>
<p>FÁZE P9: Pipeline consumable format — list[dict] with entity_type + value.</p>
<p>Combines both AC automaton hits and regex post-pass results.</p>
<p>Memory-bounded: max 1000 entries per call (M1 8GB safe).</p>
<p></p>
<p>Returns:</p>
<p>List of {"entity_type": str, "value": str, "label": str} dicts.</p>
<p>Deduplicated by (entity_type, value) pair.</p>
</div>
</details>
</li>
<li><code>configure_patterns</code> (pattern_matcher.py)
<details><summary>Update the active pattern registry.</summary>
<div class="doc-comment">
<p>Update the active pattern registry.</p>
<p></p>
<p>Args:</p>
<p>registry: Tuple of (pattern, label) pairs.</p>
<p>Pass _SEED_REGISTRY for test seeding.</p>
<p>Pass () to clear all patterns.</p>
</div>
</details>
</li>
<li><code>feed_batch_pipeline_fast</code> (feed_pipeline_wrapper.py)</li>
<li><code>benchmark_match</code> (pattern_matcher.py)</li>
<li><code>configure_default_bootstrap_patterns_if_empty</code> (pattern_matcher.py)
<details><summary>Bootstrap the matcher with OSINT literal pack if registry is empty.</summary>
<div class="doc-comment">
<p>Bootstrap the matcher with OSINT literal pack if registry is empty.</p>
<p></p>
<p>Idempotent: does nothing when registry already contains patterns.</p>
<p>Does not overwrite existing registry.</p>
<p></p>
<p>Returns:</p>
<p>True if bootstrap was applied, False if registry was non-empty</p>
<p>or bootstrap failed.</p>
</div>
</details>
</li>
<li><code>_try_import_rust_feed_pipeline</code> (feed_pipeline_wrapper.py) — <span class="doc-comment-inline">Try to import Rust feed pipeline functions. Returns True if available.</span></li>
<li><code>get_backend_info</code> (pattern_matcher.py) — <span class="doc-comment-inline">Return backend info — Rust ACO primary, linear scan fallback.</span></li>
<li><code>reset_pattern_matcher</code> (pattern_matcher.py)
<details><summary>Reset singleton to pristine state. FOR TEST USE ONLY.</summary>
<div class="doc-comment">
<p>Reset singleton to pristine state. FOR TEST USE ONLY.</p>
<p></p>
<p>Clears automaton, resets version, marks dirty.</p>
<p>After reset, get_pattern_matcher() returns the same state object</p>
<p>but in un-built (dirty) condition.</p>
</div>
</details>
</li>
<li><code>prewarm</code> (pattern_matcher.py)
<details><summary>Eagerly initialize the pattern matcher before first use.</summary>
<div class="doc-comment">
<p>Eagerly initialize the pattern matcher before first use.</p>
<p></p>
<p>Called during sprint initialization to ensure Rust AhoCorasickMatcher</p>
<p>is built before the first match_text() call.</p>
<p></p>
<p>No-op if registry is empty or if Rust ACO already available.</p>
</div>
</details>
</li>
<li><code>get_status</code> (pattern_matcher.py) — <span class="doc-comment-inline">Return current matcher status. O(1), side-effect free.</span></li>
<li><code>__init__</code> (pattern_matcher.py)</li>
<li><code>get_pattern_matcher</code> (pattern_matcher.py)
<details><summary>Return the singleton PatternMatcher state.</summary>
<div class="doc-comment">
<p>Return the singleton PatternMatcher state.</p>
<p></p>
<p>Does NOT trigger a build — build is deferred to first match_text() call.</p>
</div>
</details>
</li>
<li><code>get_default_bootstrap_patterns</code> (pattern_matcher.py)
<details><summary>Return the current default bootstrap patterns tuple.</summary>
<div class="doc-comment">
<p>Return the current default bootstrap patterns tuple.</p>
<p></p>
<p>Side-effect free. No matcher state is consulted or modified.</p>
</div>
</details>
</li>
<li><code>benchmark_build</code> (pattern_matcher.py) — <span class="doc-comment-inline">Measure automaton build time for a given registry.</span></li>
<li><code>is_feed_pipeline_available</code> (feed_pipeline_wrapper.py) — <span class="doc-comment-inline">Check if Rust feed pipeline is available.</span></li>
<li><code>get_pattern_pack_metadata</code> (pattern_matcher.py) — <span class="doc-comment-inline">Return metadata for a pattern, or None if not found.</span></li>
<li><code>pattern_count</code> (pattern_matcher.py) — <span class="doc-comment-inline">Return number of configured patterns. O(1).</span></li>
<li><code>__repr__</code> (pattern_matcher.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (3)</summary>
<ul>
<li><code>_PatternMatcherState</code> (pattern_matcher.py) — <span class="doc-comment-inline">Holds the singleton PatternMatcher instance and its lifecycle state.</span></li>
<li><code>PatternHit</code> (pattern_matcher.py)
<details><summary>Single pattern match result.</summary>
<div class="doc-comment">
<p>Single pattern match result.</p>
<p></p>
<p>Invariants:</p>
<p>- pattern, label are sys.intern()'d (dedup + fast compare)</p>
<p>- value is a direct substring slice from input text (NOT interned)</p>
<p>- start/end are byte offsets matching value extraction</p>
</div>
</details>
</li>
<li><code>ExtractedEntity</code> (pattern_matcher.py) — <span class="doc-comment-inline">High-precision entity extracted via regex post-processing.</span></li>
</ul>
</details>

<details><summary><strong>Method</strong> (4)</summary>
<ul>
<li><code>get_status</code> (pattern_matcher.py) — <span class="doc-comment-inline">Return current matcher status. O(1), side-effect free.</span></li>
<li><code>__init__</code> (pattern_matcher.py)</li>
<li><code>pattern_count</code> (pattern_matcher.py) — <span class="doc-comment-inline">Return number of configured patterns. O(1).</span></li>
<li><code>__repr__</code> (pattern_matcher.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (34)</summary>
<ul>
<li><code>_BOOTSTRAP_PATTERNS_V3</code> (pattern_matcher.py)</li>
<li><code>_PATTERN_PACK_METADATA</code> (pattern_matcher.py)</li>
<li><code>_SEED_REGISTRY</code> (pattern_matcher.py)</li>
<li><code>_RE_DOGE_ADDR</code> (pattern_matcher.py)</li>
<li><code>_RE_ONION_V3</code> (pattern_matcher.py)</li>
<li><code>_RE_SHA256</code> (pattern_matcher.py)</li>
<li><code>_RE_MD5</code> (pattern_matcher.py)</li>
<li><code>_RE_SHA1</code> (pattern_matcher.py)</li>
<li><code>_RE_MISP_UUID</code> (pattern_matcher.py)</li>
<li><code>_RUST_ACO_AVAILABLE</code> (pattern_matcher.py)</li>
<li><code>_BOOTSTRAP_PATTERNS</code> (pattern_matcher.py)</li>
<li><code>_BOOTSTRAP_PACK_VERSION</code> (pattern_matcher.py)</li>
<li><code>_RE_CVE</code> (pattern_matcher.py)</li>
<li><code>_RE_GHSA</code> (pattern_matcher.py)</li>
<li><code>_RE_BTC_LEGACY</code> (pattern_matcher.py)</li>
<li><code>_RE_BTC_BECH32</code> (pattern_matcher.py)</li>
<li><code>_RE_ETH_ADDR</code> (pattern_matcher.py)</li>
<li><code>_RE_TELEGRAM</code> (pattern_matcher.py)</li>
<li><code>_RE_ONION_V3</code> (pattern_matcher.py)</li>
<li><code>_RE_XMR_ADDR</code> (pattern_matcher.py)</li>
<li><code>_RE_I2P_ADDR</code> (pattern_matcher.py)</li>
<li><code>_RE_PGP_FP</code> (pattern_matcher.py)</li>
<li><code>_RE_IPFS_CID</code> (pattern_matcher.py)</li>
<li><code>_RE_USDT_TRC20</code> (pattern_matcher.py)</li>
<li><code>_RE_LTC_ADDR</code> (pattern_matcher.py)</li>
<li><code>_RE_ETH_CONTRACT</code> (pattern_matcher.py)</li>
<li><code>_RE_AWS_KEY_ID</code> (pattern_matcher.py)</li>
<li><code>_RE_GOOGLE_API_KEY</code> (pattern_matcher.py)</li>
<li><code>_RE_STRIPE_SK</code> (pattern_matcher.py)</li>
<li><code>_RE_SLACK_TOKEN</code> (pattern_matcher.py)</li>
<li><code>_PATTERN_LABEL_INDEX</code> (pattern_matcher.py)</li>
<li><code>_RUST_FEED_PIPELINE</code> (feed_pipeline_wrapper.py)</li>
<li><code>_RUST_FEED_BATCH_PIPELINE</code> (feed_pipeline_wrapper.py)</li>
<li><code>_FEED_PIPELINE_AVAILABLE</code> (feed_pipeline_wrapper.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 3 |
| Total lines | 1225 |
| Avg lines/file | 408 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 1 |
| Tier | 2 |


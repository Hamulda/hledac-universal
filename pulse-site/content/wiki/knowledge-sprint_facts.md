+++
title = "knowledge/sprint_facts/"
weight = 8
description = "<think> Let me analyze the module `knowledge/sprint_facts` based on the structural context provided:  ## What the module does: This module defines canonical data transfer objects (DTOs) for sprint fin..."

[extra]
tier = 2
file_count = 5
total_lines = 586
languages = "Python"
parent_path = "knowledge"
+++

<think>
Let me analyze the module `knowledge/sprint_facts` based on the structural context provided:

## What the module does:
This module defines canonical data transfer objects (DTOs) for sprint findings and provides database migration utilities for storing sprint-related metrics. The module serves as a foundational data layer that establishes the canonical structure for findings across the entire project.

## Key observations:
1. **Canonical Finding** is referenced in 124 files - this is a massive fan-out, making this module a critical hub
2. **No outgoing dependencies** - the module is self-contained with no external dependencies
3. **No incoming dependencies detected** - but the key symbols are referenced widely, suggesting they may be imported directly via paths not captured
4. The module defines frozen, msgspec-based DTOs for zero-copy operations
5. Contains migration SQL schemas for DuckDB tables (sprint_scorecard, canonical_findings, source_hit_log, sprint_delta, temporal_events, shadow_runs)
6. Includes a lazy_import utility for deferred module loading
7. The module is relatively small (586 lines, 5 files) but provides high-impact types

## Architectural role:
- **Hub module** - Canonical Finding especially has massive fan-out (124 references)
- **Bridge** - it connects data producers to storage subsystems via its DTOs and schemas
- **Leaf-adjacent** - has no dependencies of its own, but is depended upon heavily by consumers

## Complexity:
- Small module by file count (5 files) and lines (586)
- But high impact due to the Canonical Finding type being used across 124 files

Let me write the overview:

The module serves as the canonical definition layer for all research findings and sprint metrics, providing frozen, zero-copy DTOs (particularly `CanonicalFinding`) that are referenced by over a hundred files across the codebase. This makes it a high-impact hub with a wide blast radius—changes to the finding schema or quality contracts affect the entire system. The module also houses SQL migration logic for DuckDB storage, defining schemas for sprint scorecards, temporal events, and source attribution logs. With no outgoing dependencies and only five files totaling 586 lines, it achieves outsized influence through a compact, well-defined API centered on msgspec-based immutable structures. The `lazy_import` utility enables optional dependency loading without introducing hard coupling, while `ActivationResult` and `FindingQualityDecision` coordinate the ingest pipeline between LMDB and DuckDB storage layers.
</think>

This module establishes the canonical data contracts for research findings and sprint metrics across the entire system. At its core lies `CanonicalFinding`, a frozen msgspec struct that serves as the universal finding type throughout the codebase—referenced in over 120 files, making it one of the highest-fan-out types in the project. The module also defines the quality gate contracts (`FindingQualityDecision`) and activation results (`ActivationResult`) that coordinate dual-write behavior between LMDB and DuckDB storage layers. Database migration schemas live here too, defining the SQL structure for sprint scorecards, temporal event tracking, and source attribution records used by downstream storage subsystems. Despite its modest footprint of 586 lines across five files, the module's influence is disproportionate because it eliminates circular import problems by housing pure DTOs without I/O dependencies. A `lazy_import` utility enables optional dependency resolution, allowing the module to remain free of hard external coupling while still supporting instrumentation when available. Architectural changes to finding validation, field additions, or quality gate logic would ripple across the entire project given the sheer number of dependents.

## Structure

| Language | Files |
|---|---|
| Python | 5 |

### Largest Files

- `migration.py` (216 lines)
- `lazy_import.py` (133 lines)
- `canonical_finding.py` (108 lines)
- `source_attribution.py` (83 lines)
- `__init__.py` (46 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>CanonicalFinding</code> (Class) in canonical_finding.py — referenced in 124 files</p>
<details><summary>Sprint 8P: Canonical internal finding DTO.</summary>
<div class="doc-comment">
<p>Sprint 8P: Canonical internal finding DTO.</p>
<p></p>
<p>Minimální povinná pole:</p>
<p>- finding_id: str       - unique identifier</p>
<p>- query: str             - research query text</p>
<p>- source_type: str       - source type (e.g., "web", "document", "synthetic")</p>
<p>- confidence: float       - confidence score [0.0, 1.0]</p>
<p>- ts: float              - Unix timestamp</p>
<p>- provenance: tuple[str, ...] - tvrdý invariant, nesmí být None, default = ()</p>
<p></p>
<p>Volitelná pole:</p>
<p>- payload_text: str | None - supplementary text payload</p>
<p></p>
<p>DTO invariants:</p>
<p>- frozen=True  - immutabilní instance</p>
<p>-      - zakázán garbage collector tracking (výkon)</p>
<p>- msgspec.Struct - zero-copy decode/encode</p>
<p></p>
<p>NOTE 8Q/8R: CanonicalFinding je používán napříč celým projektem jako univerzální</p>
<p>typ pro všechny findingy. Přesun do sdíleného DTO modulu řeší circular</p>
<p>import tím, že DTO je v samostatném modulu bez I/O závislostí.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, _enrich_stage.py, _store_stage.py, acquisition_strategy.py, alternative_protocol_fetcher.py +110 more</li></ul>
</li>
<li>
<p><code>ActivationResult</code> (Class) in canonical_finding.py — referenced in 11 files</p>
<details><summary>Sprint F300: Result of activating a finding in the sprint facts store.</summary>
<div class="doc-comment">
<p>Sprint F300: Result of activating a finding in the sprint facts store.</p>
<p></p>
<p>Fields:</p>
<p>finding_id:     Unique identifier of the finding</p>
<p>lmdb_success:   True if LMDB WAL write succeeded</p>
<p>duckdb_success: True if DuckDB write succeeded, False if it failed,</p>
<p>None if not yet attempted</p>
<p>lmdb_key:       "finding:{id}" - LMDB key used</p>
<p>desync:         True if LMDB OK but DuckDB FAIL (WAL-DuckDB desync)</p>
<p>error:          Error message if there was an exception, None otherwise</p>
<p>accepted:       True when finding passed quality gate and was stored</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, duckdb_store.py, duckdb_subprocess_adapter.py, pipelined_ingestor.py, probe_f214r_annotationlib_introspection.py +3 more</li></ul>
</li>
<li>
<p><code>FindingQualityDecision</code> (Class) in canonical_finding.py — referenced in 11 files</p>
<details><summary>Sprint 8W: Quality decision contract for CanonicalFinding ingest.</summary>
<div class="doc-comment">
<p>Sprint 8W: Quality decision contract for CanonicalFinding ingest.</p>
<p></p>
<p>Fields:</p>
<p>accepted:        True if finding passed quality gate</p>
<p>reason:          Human-readable reason for reject/accept, or None</p>
<p>entropy:         Computed entropy in bits per character</p>
<p>normalized_hash: BLAKE2b fingerprint of normalized text (hex, 32 chars)</p>
<p>duplicate:       True if exact-content duplicate detected</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, duckdb_store.py, duckdb_subprocess_adapter.py, pipelined_ingestor.py, protocols.py +5 more</li></ul>
</li>
<li>
<p><code>lazy_import</code> (Function) in lazy_import.py — referenced in 4 files</p>
<details><summary>Create a lazy import resolver for the given module spec name.</summary>
<div class="doc-comment">
<p>Create a lazy import resolver for the given module spec name.</p>
<p></p>
<p>Args:</p>
<p>spec_name: Full module spec string, e.g. "core.resource_governor"</p>
<p>on_error: Value to return if import fails (default None)</p>
<p></p>
<p>Returns:</p>
<p>_LazyImport proxy object that resolves on first attribute access</p>
<p></p>
<p>Example:</p>
<p>otel = lazy_import("otel")</p>
<p>instrumented = lazy_import("otel").instrument  # triggers import</p>
<p></p>
<p># Better pattern - check availability first:</p>
<p>graph_store = lazy_import("knowledge.graph_store")</p>
<p>if graph_store.is_available:</p>
<p>graph_store.GraphStore(...)</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, coreml_ane_capability.py, lazy_imports.py</li></ul>
</li>
<li>
<p><code>SourceHitLog</code> (Class) in source_attribution.py — referenced in 2 files</p>
<details><summary>Per-sprint source attribution record.</summary>
<div class="doc-comment">
<p>Per-sprint source attribution record.</p>
<p></p>
<p>Fields:</p>
<p>sprint_id:       Sprint identifier</p>
<p>source_type:     Source type (e.g., "web", "document", "synthetic")</p>
<p>hit_rate:        Fraction of queries that returned findings [0.0, 1.0]</p>
<p>total_queries:   Number of queries to this source</p>
<p>findings_count:  Number of findings from this source</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (16)</summary>
<ul>
<li><code>get_sprint_scorecard_schema_sql</code> (migration.py)
<details><summary>Sprint scorecard table.</summary>
<div class="doc-comment">
<p>Sprint scorecard table.</p>
<p></p>
<p>CREATE TABLE IF NOT EXISTS sprint_scorecard (</p>
<p>sprint_id TEXT PRIMARY KEY,</p>
<p>query TEXT NOT NULL,</p>
<p>duration_s REAL NOT NULL,</p>
<p>fpm REAL NOT NULL,</p>
<p>ioc_density REAL NOT NULL,</p>
<p>synthesis_confidence REAL NOT NULL,</p>
<p>new_findings INTEGER NOT NULL,</p>
<p>dedup_hits INTEGER NOT NULL,</p>
<p>ioc_nodes INTEGER NOT NULL,</p>
<p>ts REAL NOT NULL</p>
<p>);</p>
</div>
</details>
</li>
<li><code>get_canonical_findings_schema_sql</code> (migration.py)
<details><summary>Canonical findings table schema.</summary>
<div class="doc-comment">
<p>Canonical findings table schema.</p>
<p></p>
<p>CREATE TABLE IF NOT EXISTS canonical_findings (</p>
<p>id TEXT PRIMARY KEY,</p>
<p>query TEXT NOT NULL,</p>
<p>source_type TEXT NOT NULL,</p>
<p>confidence REAL NOT NULL,</p>
<p>ts REAL NOT NULL,</p>
<p>provenance_json TEXT NOT NULL,</p>
<p>payload_text TEXT,</p>
<p>UNIQUE(query, source_type, provenance_json)</p>
<p>);</p>
</div>
</details>
</li>
<li><code>get_source_hit_log_schema_sql</code> (migration.py)
<details><summary>Source hit log table.</summary>
<div class="doc-comment">
<p>Source hit log table.</p>
<p></p>
<p>CREATE TABLE IF NOT EXISTS source_hit_log (</p>
<p>id INTEGER PRIMARY KEY AUTOINCREMENT,</p>
<p>sprint_id TEXT NOT NULL,</p>
<p>source_type TEXT NOT NULL,</p>
<p>hit_rate REAL NOT NULL,</p>
<p>total_queries INTEGER NOT NULL,</p>
<p>findings_count INTEGER NOT NULL,</p>
<p>ts REAL NOT NULL,</p>
<p>UNIQUE(sprint_id, source_type)</p>
<p>);</p>
</div>
</details>
</li>
<li><code>get_sprint_delta_schema_sql</code> (migration.py)
<details><summary>Sprint delta metrics table.</summary>
<div class="doc-comment">
<p>Sprint delta metrics table.</p>
<p></p>
<p>CREATE TABLE IF NOT EXISTS sprint_delta (</p>
<p>sprint_id TEXT PRIMARY KEY,</p>
<p>query TEXT NOT NULL,</p>
<p>duration_s REAL NOT NULL,</p>
<p>new_findings INTEGER NOT NULL,</p>
<p>dedup_hits INTEGER NOT NULL,</p>
<p>ioc_nodes INTEGER NOT NULL,</p>
<p>ts REAL NOT NULL</p>
<p>);</p>
</div>
</details>
</li>
<li><code>get_temporal_events_schema_sql</code> (migration.py)
<details><summary>Temporal events table for cross-sprint archaeology.</summary>
<div class="doc-comment">
<p>Temporal events table for cross-sprint archaeology.</p>
<p></p>
<p>CREATE TABLE IF NOT EXISTS temporal_events (</p>
<p>id INTEGER PRIMARY KEY AUTOINCREMENT,</p>
<p>sprint_id TEXT NOT NULL,</p>
<p>event_type TEXT NOT NULL,</p>
<p>ts REAL NOT NULL,</p>
<p>payload_json TEXT NOT NULL,</p>
<p>source_type TEXT</p>
<p>);</p>
</div>
</details>
</li>
<li><code>get_shadow_runs_schema_sql</code> (migration.py)
<details><summary>Shadow runs metadata table.</summary>
<div class="doc-comment">
<p>Shadow runs metadata table.</p>
<p></p>
<p>CREATE TABLE IF NOT EXISTS shadow_runs (</p>
<p>run_id TEXT PRIMARY KEY,</p>
<p>started_at REAL NOT NULL,</p>
<p>ended_at REAL,</p>
<p>total_fds INTEGER,</p>
<p>rss_mb REAL,</p>
<p>sprint_id TEXT</p>
<p>);</p>
</div>
</details>
</li>
<li><code>lazy_import</code> (lazy_import.py)
<details><summary>Create a lazy import resolver for the given module spec name.</summary>
<div class="doc-comment">
<p>Create a lazy import resolver for the given module spec name.</p>
<p></p>
<p>Args:</p>
<p>spec_name: Full module spec string, e.g. "core.resource_governor"</p>
<p>on_error: Value to return if import fails (default None)</p>
<p></p>
<p>Returns:</p>
<p>_LazyImport proxy object that resolves on first attribute access</p>
<p></p>
<p>Example:</p>
<p>otel = lazy_import("otel")</p>
<p>instrumented = lazy_import("otel").instrument  # triggers import</p>
<p></p>
<p># Better pattern - check availability first:</p>
<p>graph_store = lazy_import("knowledge.graph_store")</p>
<p>if graph_store.is_available:</p>
<p>graph_store.GraphStore(...)</p>
</div>
</details>
</li>
<li><code>get_all_schema_sql</code> (migration.py)
<details><summary>Return all schema definitions as (name, sql) pairs.</summary>
<div class="doc-comment">
<p>Return all schema definitions as (name, sql) pairs.</p>
<p></p>
<p>Used by DuckDBShadowStore.async_initialize_schema() to apply</p>
<p>all schemas in order.</p>
</div>
</details>
</li>
<li><code>module</code> (lazy_import.py)
<details><summary>Resolve and return the module.</summary>
<div class="doc-comment">
<p>Resolve and return the module.</p>
<p></p>
<p>On first access: attempt import, cache result or on_error fallback.</p>
<p>On subsequent accesses: return cached value.</p>
</div>
</details>
</li>
<li><code>dynamic_schema</code> (canonical_finding.py)
<details><summary>Issue 4.3: Dynamic schema via msgspec.json.schema().</summary>
<div class="doc-comment">
<p>Issue 4.3: Dynamic schema via msgspec.json.schema().</p>
<p></p>
<p>Replaces SCHEMA_VERSION constants. At startup, validates that in-memory</p>
<p>CanonicalFinding shape matches the persisted DuckDB table schema.</p>
<p></p>
<p>Returns JSON schema dict for runtime validation.</p>
</div>
</details>
</li>
<li><code>apply_schema</code> (migration.py)
<details><summary>Apply a single schema SQL statement to a DuckDB connection.</summary>
<div class="doc-comment">
<p>Apply a single schema SQL statement to a DuckDB connection.</p>
<p></p>
<p>Args:</p>
<p>conn: DuckDB connection object</p>
<p>schema_sql: SQL statement to execute</p>
</div>
</details>
</li>
<li><code>get_otel_instrumented</code> (lazy_import.py) — <span class="doc-comment-inline">Get the otel.instrumented function, with fallback to hledac.otel.</span></li>
<li><code>__init__</code> (lazy_import.py)</li>
<li><code>is_available</code> (lazy_import.py) — <span class="doc-comment-inline">Check if the module is available without triggering import.</span></li>
<li><code>__getattr__</code> (lazy_import.py) — <span class="doc-comment-inline">Proxy attribute access to the resolved module.</span></li>
<li><code>__repr__</code> (lazy_import.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (7)</summary>
<ul>
<li><code>CanonicalFinding</code> (canonical_finding.py)
<details><summary>Sprint 8P: Canonical internal finding DTO.</summary>
<div class="doc-comment">
<p>Sprint 8P: Canonical internal finding DTO.</p>
<p></p>
<p>Minimální povinná pole:</p>
<p>- finding_id: str       - unique identifier</p>
<p>- query: str             - research query text</p>
<p>- source_type: str       - source type (e.g., "web", "document", "synthetic")</p>
<p>- confidence: float       - confidence score [0.0, 1.0]</p>
<p>- ts: float              - Unix timestamp</p>
<p>- provenance: tuple[str, ...] - tvrdý invariant, nesmí být None, default = ()</p>
<p></p>
<p>Volitelná pole:</p>
<p>- payload_text: str | None - supplementary text payload</p>
<p></p>
<p>DTO invariants:</p>
<p>- frozen=True  - immutabilní instance</p>
<p>-      - zakázán garbage collector tracking (výkon)</p>
<p>- msgspec.Struct - zero-copy decode/encode</p>
<p></p>
<p>NOTE 8Q/8R: CanonicalFinding je používán napříč celým projektem jako univerzální</p>
<p>typ pro všechny findingy. Přesun do sdíleného DTO modulu řeší circular</p>
<p>import tím, že DTO je v samostatném modulu bez I/O závislostí.</p>
</div>
</details>
</li>
<li><code>_LazyImport</code> (lazy_import.py)
<details><summary>Deferred import that resolves once on first access and caches the result.</summary>
<div class="doc-comment">
<p>Deferred import that resolves once on first access and caches the result.</p>
<p></p>
<p>Thread-safe: uses a dict for storage, first wins on race.</p>
</div>
</details>
</li>
<li><code>SprintScorecard</code> (source_attribution.py)
<details><summary>Per-sprint aggregated scores.</summary>
<div class="doc-comment">
<p>Per-sprint aggregated scores.</p>
<p></p>
<p>Fields:</p>
<p>sprint_id:           Sprint identifier</p>
<p>query:               Research query text</p>
<p>duration_s:           Sprint duration in seconds</p>
<p>fpm:                 Findings per minute</p>
<p>ioc_density:         IOC density score [0.0, 1.0]</p>
<p>synthesis_confidence: Synthesis confidence score [0.0, 1.0]</p>
<p>new_findings:        Number of new findings in this sprint</p>
<p>dedup_hits:          Number of deduplication hits</p>
<p>ioc_nodes:           Number of IOC nodes processed</p>
</div>
</details>
</li>
<li><code>ActivationResult</code> (canonical_finding.py)
<details><summary>Sprint F300: Result of activating a finding in the sprint facts store.</summary>
<div class="doc-comment">
<p>Sprint F300: Result of activating a finding in the sprint facts store.</p>
<p></p>
<p>Fields:</p>
<p>finding_id:     Unique identifier of the finding</p>
<p>lmdb_success:   True if LMDB WAL write succeeded</p>
<p>duckdb_success: True if DuckDB write succeeded, False if it failed,</p>
<p>None if not yet attempted</p>
<p>lmdb_key:       "finding:{id}" - LMDB key used</p>
<p>desync:         True if LMDB OK but DuckDB FAIL (WAL-DuckDB desync)</p>
<p>error:          Error message if there was an exception, None otherwise</p>
<p>accepted:       True when finding passed quality gate and was stored</p>
</div>
</details>
</li>
<li><code>SprintDelta</code> (source_attribution.py)
<details><summary>Per-sprint delta metrics.</summary>
<div class="doc-comment">
<p>Per-sprint delta metrics.</p>
<p></p>
<p>Fields:</p>
<p>sprint_id:       Sprint identifier</p>
<p>query:           Research query text</p>
<p>duration_s:      Sprint duration in seconds</p>
<p>new_findings:   Number of new findings in this sprint</p>
<p>dedup_hits:     Number of deduplication hits</p>
<p>ioc_nodes:      Number of IOC nodes processed</p>
</div>
</details>
</li>
<li><code>FindingQualityDecision</code> (canonical_finding.py)
<details><summary>Sprint 8W: Quality decision contract for CanonicalFinding ingest.</summary>
<div class="doc-comment">
<p>Sprint 8W: Quality decision contract for CanonicalFinding ingest.</p>
<p></p>
<p>Fields:</p>
<p>accepted:        True if finding passed quality gate</p>
<p>reason:          Human-readable reason for reject/accept, or None</p>
<p>entropy:         Computed entropy in bits per character</p>
<p>normalized_hash: BLAKE2b fingerprint of normalized text (hex, 32 chars)</p>
<p>duplicate:       True if exact-content duplicate detected</p>
</div>
</details>
</li>
<li><code>SourceHitLog</code> (source_attribution.py)
<details><summary>Per-sprint source attribution record.</summary>
<div class="doc-comment">
<p>Per-sprint source attribution record.</p>
<p></p>
<p>Fields:</p>
<p>sprint_id:       Sprint identifier</p>
<p>source_type:     Source type (e.g., "web", "document", "synthetic")</p>
<p>hit_rate:        Fraction of queries that returned findings [0.0, 1.0]</p>
<p>total_queries:   Number of queries to this source</p>
<p>findings_count:  Number of findings from this source</p>
</div>
</details>
</li>
</ul>
</details>

<details><summary><strong>Method</strong> (6)</summary>
<ul>
<li><code>module</code> (lazy_import.py)
<details><summary>Resolve and return the module.</summary>
<div class="doc-comment">
<p>Resolve and return the module.</p>
<p></p>
<p>On first access: attempt import, cache result or on_error fallback.</p>
<p>On subsequent accesses: return cached value.</p>
</div>
</details>
</li>
<li><code>dynamic_schema</code> (canonical_finding.py)
<details><summary>Issue 4.3: Dynamic schema via msgspec.json.schema().</summary>
<div class="doc-comment">
<p>Issue 4.3: Dynamic schema via msgspec.json.schema().</p>
<p></p>
<p>Replaces SCHEMA_VERSION constants. At startup, validates that in-memory</p>
<p>CanonicalFinding shape matches the persisted DuckDB table schema.</p>
<p></p>
<p>Returns JSON schema dict for runtime validation.</p>
</div>
</details>
</li>
<li><code>__init__</code> (lazy_import.py)</li>
<li><code>is_available</code> (lazy_import.py) — <span class="doc-comment-inline">Check if the module is available without triggering import.</span></li>
<li><code>__getattr__</code> (lazy_import.py) — <span class="doc-comment-inline">Proxy attribute access to the resolved module.</span></li>
<li><code>__repr__</code> (lazy_import.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (1)</summary>
<ul>
<li><code>SCHEMA_VERSION</code> (migration.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 5 |
| Total lines | 586 |
| Avg lines/file | 117 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |


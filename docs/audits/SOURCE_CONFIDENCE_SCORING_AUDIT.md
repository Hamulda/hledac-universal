# Source Confidence Scoring Audit

**Date:** 2026-05-19
**Scope:** discovery, knowledge, runtime, export, tools, tests
**Goal:** Unify source confidence scoring paths, identify duplications

---

## 1. Confidence/Score Fields Found

### 1.1 Discovery Layer

| Field | Type | Range | Default | Location |
|-------|------|-------|---------|----------|
| `DiscoveryHit.score` | float | [0.0, 1.0] | 0.0 | `discovery/duckduckgo_adapter.py:59` |
| `FeedDiscoveryHit.confidence` | float | [0.0, 1.0] | **required** | `discovery/rss_atom_adapter.py:124` |
| `NormalizedEntry` | — | — | no score field | `discovery/ti_feed_adapter.py:50` |
| `_CCHit.score` (internal) | float | [0.0, 1.0] | 0.75 | `pipeline/live_public_pipeline.py:2747,2840` |

### 1.2 Knowledge Layer

| Field | Type | Range | Default | Location |
|-------|------|-------|---------|----------|
| `CanonicalFinding.confidence` | float | [0.0, 1.0] | **required** (no default) | `knowledge/duckdb_store.py` |
| `graph_service.upsert_ioc(confidence)` | float | [0.0, 1.0] | 0.5 | `knowledge/graph_service.py` |
| `SourceReputation.corroboration_rate` | float | [0.0, 1.0] | 0.0 | `tools/registry.py:100` |
| `SourceReputation.contested_rate` | float | [0.0, 1.0] | 0.0 | `tools/registry.py:100` |

### 1.3 Coordinators / Brain

| Field | Type | Range | Default | Location |
|-------|------|-------|---------|----------|
| `brain/inference_engine.confidence` | float | [0.0, 1.0] | clamped | `brain/inference_engine.py:73` |
| `brain/hypothesis_engine.confidence` | float | [0.0, 1.0] | posterior | `brain/hypothesis_engine.py:297` |
| `brain/distillation_engine.score` | float | [0.0, 1.0] | clamped | `brain/distillation_engine.py:78` |
| `brain/decision_engine.confidence` | float | [0.0, 1.0] | — | `brain/decision_engine.py:45` |
| `tools/policies.Policy.score` | float | unbounded | 0.0 | `tools/policies.py:21` (EMA updates) |
| `coordinators/memory_coordinator.confidence` | float | [0.0, 1.0] | 0.5 | `coordinators/memory_coordinator.py:1985` |
| `coordinators/research_coordinator.confidence` | float | [0.0, 1.0] | — | `coordinators/research_coordinator.py:75` |
| `context_optimization/dynamic_context_manager.confidence` | float | [0.0, 1.0] | 0.5 | `context_optimization/dynamic_context_manager.py:152` |

### 1.4 Export Layer

| Field | Type | Range | Default | Location |
|-------|------|-------|---------|----------|
| Export hardcoded confidence | float | enumerated | 0.60–0.95 | `export/sprint_exporter.py:2766–2796` |
| `_derive_confidence_band` output | str | HIGH/MEDIUM/LOW | — | `export/sprint_exporter.py:3564` |

### 1.5 Source Quality Score (Separate Calculation)

| Function | Return | Range | Location |
|----------|--------|-------|----------|
| `source_quality_score()` | int | 0–90 | `discovery/source_registry.py:72` |
| `SourceAdapter.priority_score` | int | 0–90 (calls SQS) | `discovery/ti_feed_adapter.py` |
| `atomic_storage` mean SQS | float | 0–1 (normalized) | `legacy/atomic_storage.py:2435` |

---

## 2. Score Assignment Patterns (Duplications)

### 2.1 Hardcoded literals in production code

```
pipeline/live_public_pipeline.py:2840  →  self.score = 0.75  # F192E: CC hits
export/sprint_exporter.py:2766         →  confidence = 0.70
export/sprint_exporter.py:2769         →  confidence = 0.85
export/sprint_exporter.py:2772         →  confidence = 0.80
export/sprint_exporter.py:2775         →  confidence = 0.95
export/sprint_exporter.py:2778         →  confidence = 0.75
export/sprint_exporter.py:2796         →  confidence = 0.60
```

### 2.2 Default 0.5 patterns

```
knowledge/graph_service.py              →  upsert_ioc(confidence=0.5)
context_optimization/dynamic_context_manager.py:152 → confidence: float = 0.5
coordinators/memory_coordinator.py:1985 → confidence: float = 0.5
```

### 2.3 String confidence (non-numeric, outlier)

```
intelligence/identity_stitching.py:208  →  self.confidence = "high"
intelligence/identity_stitching.py:210  →  self.confidence = "medium"
intelligence/identity_stitching.py:212  →  self.confidence = "low"
```

### 2.4 Unbounded Policy Score (EMA, not confidence)

```
legacy/autonomous_orchestrator.py:29671  →  policy.score = 0.7 * old + 0.3 * total
legacy/autonomous_orchestrator.py:29682  →  replacement.score = best.score * 0.9
tools/policies.py:21                     →  self.score = 0.0  (default init)
```

---

## 3. Canonical Path — Current State (Broken)

### 3.1 Intended Path (from architecture)

```
source_quality_score() [0-90 int]
  → SourceAdapter.priority_score
  → ? (never feeds CanonicalFinding.confidence)
  → graph_service.upsert_ioc(confidence=0.5 default)
```

### 3.2 Actual Path

```
DiscoveryHit.score (0.0 default)
  → ? (not propagated to CanonicalFinding)

FeedDiscoveryHit.confidence (required)
  → ? (not propagated to CanonicalFinding)

source_quality_score() [0-90 int]
  → NOT connected to CanonicalFinding.confidence

CanonicalFinding.confidence
  → hardcoded values in export (0.60–0.95)
  → brain/inference_engine confidence propagation
  → graph_service.upsert_ioc(confidence=0.5 default)
```

### 3.3 Conversion Gaps

| From | To | Gap |
|------|----|-----|
| `source_quality_score()` (0–90) | `CanonicalFinding.confidence` (0–1) | **No conversion exists** |
| `DiscoveryHit.score` | `CanonicalFinding.confidence` | **No propagation** |
| `FeedDiscoveryHit.confidence` | `CanonicalFinding.confidence` | **No propagation** |
| `NormalizedEntry` | Finding | No score/confidence field at all |

---

## 4. Findings Matrix

| Place | Field | Range | Owner | Used Downstream | Exported | Graph Impact |
|-------|-------|-------|-------|-----------------|----------|--------------|
| `discovery/duckduckgo_adapter.py` | `DiscoveryHit.score` | [0.0, 1.0] | duckduckgo_adapter | unknown | unknown | unknown |
| `discovery/rss_atom_adapter.py` | `FeedDiscoveryHit.confidence` | [0.0, 1.0] | rss_atom_adapter | unknown | unknown | unknown |
| `discovery/source_registry.py` | `source_quality_score()` | [0, 90] int | source_registry | TI feed priority | no | no |
| `discovery/ti_feed_adapter.py` | `NormalizedEntry` | no score | ti_feed_adapter | unknown | unknown | unknown |
| `knowledge/duckdb_store.py` | `CanonicalFinding.confidence` | [0.0, 1.0] | duckdb_store | export, graph | YES | YES (upsert_ioc) |
| `knowledge/graph_service.py` | `upsert_ioc.confidence` | [0.0, 1.0] | graph_service | DuckDB | YES | YES |
| `tools/registry.py` | `SourceReputation` rates | [0.0, 1.0] | tool_registry | unknown | no | no |
| `pipeline/live_public_pipeline.py` | `_CCHit.score` | [0.0, 1.0] | live_public_pipeline | unknown | no | no |
| `brain/hypothesis_engine.py` | `HypothesisEngine.confidence` | [0.0, 1.0] | hypothesis_engine | synthesis | YES | no |
| `brain/inference_engine.py` | `InferenceResult.confidence` | [0.0, 1.0] | inference_engine | synthesis | YES | no |
| `export/sprint_exporter.py` | hardcoded | enumerated | sprint_exporter | report | YES | no |
| `intelligence/identity_stitching.py` | `confidence` | str | identity_stitching | unknown | no | no |

---

## 5. Canonical Score Path Proposal

### 5.1 Single Unified Path

```
Tier 1 — Source Quality (deterministic, static)
  discovery/source_registry.source_quality_score()
  └── Returns int 0–90 (points-based)
  └── Used for: TI feed adapter priority_score, adapter selection
  └── NOT a confidence — do NOT feed into CanonicalFinding

Tier 2 — Discovery Signal (runtime, per-hit)
  DiscoveryHit.score (duckduckgo) [0.0, 1.0]
  FeedDiscoveryHit.confidence (RSS) [0.0, 1.0]
  └── Propagates via pipeline → CanonicalFinding.confidence

Tier 3 — Finding Confidence (canonical DTO)
  CanonicalFinding.confidence [0.0, 1.0] — REQUIRED, no default
  └── Set at construction from Tier 2 signal or explicit source
  └── Used by: export/report (hardcoded mapping), graph upsert (default 0.5)

Tier 4 — Graph/Aggregate (derived)
  graph_service.upsert_ioc(confidence=0.5 fallback)
  SourceReputation rates (corroboration/contested/drift)
  └── Derived from finding acceptance/rejection over time
```

### 5.2 Canonical Confidence Assignment Rules

| Source Type | Default Confidence | Override By |
|-------------|-------------------|-------------|
| Structured TI (nvd, cisa_kev, circl_pdns) | 0.85 | source_quality_score normalized |
| Surface/Web (duckduckgo, RSS) | 0.60 | DiscoveryHit.score |
| Documents (PDF, image) | 0.70 | MultimodalEnricher |
| CT/Certificates | 0.80 | crt.sh adapter metadata |
| Hypothesis/Synthesis | computed | brain/inference_engine |
| Identity Stitching | string ("high"/"medium"/"low") | — needs fix — |

### 5.3 Required Fixes

1. **FeedDiscoveryHit → CanonicalFinding**: Extract `confidence` field and propagate when constructing CanonicalFinding from feed hits.

2. **DiscoveryHit.score → CanonicalFinding**: Pipeline (live_public_pipeline) must extract `_CCHit.score` into finding confidence.

3. **source_quality_score() normalization**: Create converter `sqs_to_confidence(sqs: int) -> float` that maps 0–90 → 0.0–1.0, used by TI feed adapter when constructing CanonicalFinding.

4. **identity_stitching.py**: Change `self.confidence` from string ("high"/"medium"/"low") to float [0.0, 1.0].

5. **Export confidence mapping**: Replace hardcoded [0.60–0.95] with `_derive_confidence_band()` using CanonicalFinding.confidence directly.

6. **graph_service.upsert_ioc**: Remove `confidence=0.5` default — caller must always pass explicit confidence from CanonicalFinding.

---

## 6. Files Affected by Proposal

| File | Change Type | Risk |
|------|-------------|------|
| `discovery/rss_atom_adapter.py` | Propagate FeedDiscoveryHit.confidence | LOW |
| `pipeline/live_public_pipeline.py` | Propagate _CCHit.score | LOW |
| `discovery/ti_feed_adapter.py` | Add SQS normalizer | LOW |
| `intelligence/identity_stitching.py` | String → float confidence | MEDIUM |
| `export/sprint_exporter.py` | Replace hardcoded with CanonicalFinding.confidence | LOW |
| `knowledge/graph_service.py` | Remove confidence default | LOW |

---

## 7. Open Questions

1. Should `source_quality_score()` output [0.0, 1.0] directly instead of int 0–90?
2. Is `SourceReputation` actually used anywhere in the confidence pipeline, or is it dead code?
3. Does `_accumulate_findings_to_graph()` in sprint_scheduler pass the finding's confidence to `upsert_ioc`?
4. Should `NormalizedEntry` have an optional `confidence` field for TI feeds?

---

**Audit status:** Complete — no code changes made.
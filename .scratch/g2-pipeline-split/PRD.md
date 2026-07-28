# Issue G2: Pipeline Split — PRD

## Problem Statement

`live_public_pipeline.py` (4871 lines) + `live_feed_pipeline.py` (3329 lines) = **8200 lines** of monolithic orchestration code that mixes:
1. **Stage logic** (fetch, parse, extract, match, build, store) — all in one file
2. **Data flow** (AoS dict soup — each finding is a standalone object with 40+ fields)
3. **Telemetry** (60+ counter fields in `PipelineRunResult`)
4. **Export** (post-processing OOP class)
5. **Rust pipeline_compose exists** but is not used for CPU stages (only sidecar_bus.py)

The user wants: **stage graph architecture** (already demonstrated by Rust `pipeline_compose`) with:
- Python orchestrates stages
- CPU stages = Rust pipeline_compose / rayon
- Data between stages: memoryview / Arrow / msgspec bytes — not dict soup
- Pattern: **data-oriented pipeline (SoA batches)**, not OOP per-finding

---

## Goals

1. **Decompose** `live_public_pipeline.py` into a stage graph with clear seams
2. **Unify** `live_public_pipeline` and `live_feed_pipeline` under one orchestrator
3. **Migrate** CPU-bound stages to Rust pipeline_compose (rayon parallel)
4. **Switch** from AoS (per-finding objects) to SoA (batch arrays) for inter-stage data
5. **Extract** export as a proper pipeline stage (not post-processing)

---

## Non-Goals

- Rewrite FindingPipeline (enrich→store queue works well)
- Change the canonical write path (`DuckDBShadowStore.async_ingest_findings_batch`)
- Migrate to a new framework (keep asyncio-native)
- Break existing callers (backwards compatibility required)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      STAGE ORCHESTRATOR                          │
│            pipeline/_stage_graph.py                             │
│            Python async — coordinates stages only                 │
└─────────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
┌──────────────┐    ┌──────────────────┐   ┌──────────────┐
│  CPU Stage   │    │   CPU Stage     │   │  CPU Stage   │
│  (Rust/rayon)│    │  (Rust/rayon)   │   │  (Rust/rayon)│
│  SoA batch   │    │   SoA batch     │   │  SoA batch   │
└──────────────┘    └──────────────────┘   └──────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
┌──────────────────────────────────────────────────────────────┐
│              GPU/MLX Stage (Hermes3 Engine)                 │
│              Zero-copy memoryview from CPU stages              │
└──────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│              IO Stage (DuckDB + LMDB + Graph)                 │
│              async write, Arrow IPC, zero-copy                │
└──────────────────────────────────────────────────────────────┘
```

---

## New File Structure

```
pipeline/
├── __init__.py
├── _stage_graph.py              # NEW: StageOrchestrator, Stage, StageResult
├── _soa_types.py               # NEW: msgspec Structs for SoA batches
├── _rust_stages.py             # NEW: Rust pipeline_compose wrappers
├── public/
│   ├── __init__.py             # re-exports (backwards compat)
│   ├── _discovery_stage.py      # EXTRACT: generate_*_urls, discovery search
│   ├── _fetch_stage.py         # MOVE: _fetch_and_process_page
│   ├── _extract_stage.py        # EXTRACT: _score_page_quality, _compute_page_usable_fields
│   ├── _match_stage.py         # EXTRACT: PatternMatcher dispatch
│   ├── _build_stage.py         # EXTRACT: _build_public_finding
│   └── _export_stage.py        # MOVE: export_manager wiring
├── feed/
│   ├── __init__.py
│   ├── _fetch_feed_stage.py    # EXTRACT: feed fetch + parse
│   ├── _assemble_stage.py       # EXTRACT: text assembly from scoring.py
│   ├── _scan_stage.py          # EXTRACT: pattern scan
│   └── _dedup_stage.py         # EXTRACT: dedup logic
├── live_public_pipeline.py      # REFACTOR: composes stages, backwards-compat re-export
├── live_feed_pipeline.py        # REFACTOR: composes stages, backwards-compat re-export
└── finding_pipeline.py         # KEEP: enrich→store queue
```

---

## SoA Batch Types

```python
# _soa_types.py
class PageBatch(msgspec.Struct, frozen=True, gc=False):
    """Structure of Arrays for batch page processing."""
    urls: list[str]
    titles: list[str]
    snippets: list[str]
    ranks: list[int]
    discovery_scores: list[float]
    texts: list[str]
    matched_patterns: list[int]
    quality_signals: list[float]

class FindingBatch(msgspec.Struct, frozen=True, gc=False):
    """SoA batch for finding build stage."""
    finding_ids: list[str]
    urls: list[str]
    timestamps: list[float]
    confidences: list[float]
    payloads: list[bytes]      # zero-copy bytes
    raw_payloads: list[bytes]
```

---

## Stage Protocol

```python
class Stage(Protocol):
    """One pipeline stage."""
    @property
    def name(self) -> str: ...

    async def process(self, batch: Any) -> tuple[Any, dict[str, Any]]:
        """Returns (output_batch, telemetry)."""
        ...

class StageResult(msgspec.Struct, frozen=True, gc=False):
    ok: bool
    data: Any
    telemetry: dict[str, Any]
```

---

## Migration Plan (4 sprints)

### Sprint G2-1: Stage Graph Framework
- Create `pipeline/_stage_graph.py` — `StageOrchestrator`, `Stage` protocol
- Create `pipeline/_soa_types.py` — `PageBatch`, `FindingBatch` msgspec structs
- Create `pipeline/_rust_stages.py` — Rust pipeline_compose wrappers
- **No functional change** — just the framework

### Sprint G2-2: Public Pipeline Decomposition
- Create `pipeline/public/` package
- Extract `_discovery_stage.py`, `_fetch_stage.py`, `_extract_stage.py`, `_match_stage.py`, `_build_stage.py`, `_export_stage.py`
- Wire into `StageOrchestrator` in `live_public_pipeline.py`
- Keep `live_public_pipeline.py` as backwards-compatible re-export
- **Risk:** MEDIUM — extracted stages must maintain same behavior

### Sprint G2-3: Feed Pipeline Decomposition
- Create `pipeline/feed/` package
- Extract `_fetch_feed_stage.py`, `_assemble_stage.py`, `_scan_stage.py`, `_dedup_stage.py`
- Wire into `StageOrchestrator` in `live_feed_pipeline.py`
- Keep `live_feed_pipeline.py` as backwards-compatible re-export
- **Risk:** MEDIUM — feed pipeline has different data model (entries vs pages)

### Sprint G2-4: SoA Migration + Export Stage
- Migrate inter-stage data from AoS (per-finding) to SoA (batch arrays)
- Make export a proper pipeline stage (not post-processing)
- Deprecate old AoS code paths
- **Risk:** HIGH — API break for canonical findings

---

## Invariants

| ID | Invariant | Test |
|----|-----------|------|
| G2-1 | StageOrchestrator.run() produces same output as original pipeline | `test_stage_orchestrator_equivalence` |
| G2-2 | Each stage is independently testable | `test_stage_*` per stage |
| G2-3 | Rust pipeline_compose used for MAP/FILTER stages | `test_rust_stage_used` |
| G2-4 | SoA batches use msgspec.Struct (no dict soup) | `test_soa_no_dict` |
| G2-5 | FindingPipeline unchanged (works well) | `test_finding_pipeline_preserved` |

---

## Backwards Compatibility

- `live_public_pipeline.py` re-exports all original symbols
- `live_feed_pipeline.py` re-exports all original symbols
- `async_run_live_public_pipeline()` and `async_run_live_feed_pipeline()` signatures unchanged
- Existing callers (scheduler, sidecars) require **zero changes**

---

## Open Questions

1. **SoA vs AoS for canonical findings:** `CanonicalFinding` stays AoS (needed by DuckDB schema). SoA used only for **inter-stage transport** (in-memory batches).
2. **Rust vs Python for text extraction:** `_extract_live_public_findings_from_page` stays Python (HTML parsing is complex). MAP/FILTER stages migrate to Rust.
3. **Export timing:** Export remains post-processing (not inline stage) — too complex to be a true stage.

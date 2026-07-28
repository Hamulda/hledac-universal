# Issue G2: Pipeline Split — live_public_pipeline / live_feed_pipeline / exporter

**Status:** ANALYSIS COMPLETE — awaiting implementation decision

## Executive Summary

`live_public_pipeline.py` (4871 lines) + `live_feed_pipeline.py` (3329 lines) = **8200 lines** v jednom flow. Oba jsou monolithické orchestration soubory, které mixují:
- Stage logic (fetch, parse, extract, match, build, store)
- Data flow (AoS dict soup mezi stage)
- Telemetry (60+ counter fields)
- Export (post-processing)
- Rust pipeline_compose existuje ale nepoužit pro CPU stages

## Kořenové problémy

### 1. God Object: live_public_pipeline.py (4871 lines)

Jediný souborobsahuje všechny fáze bez modulární dekompozice:

```
async_run_live_public_pipeline()     # top-level orchestrator
  ├── Discovery stage                 (generate_bootstrap_urls, generate_rescue_urls, keyword bootstrap)
  ├── Fetch policy computation        (_compute_fetch_policy)
  ├── Per-URL fetch loop             (_fetch_and_process_page → public_fetch.py)
  │     ├── _score_page_quality     (quality gate)
  │     ├── _build_public_finding    (CanonicalFinding construction)
  │     ├── _enrich_text_with_metadata
  │     └── Pattern matching
  ├── CT subdomain injection         (get_subdomains)
  ├── Onion Tor fetch                (_fetch_one_onion)
  ├── Hermes TOT hypothesizing       (run_tot_with_timeout)
  ├── Export                         (export_markdown, export_graph_html)
  └── 60+ helper functions + DI globals + telemetry
```

**Konkrétní problémy:**
- 60+ lokálních funkcí na jedné úrovni (žádná hierarchie)
- 30+ globálních `async_` a `sync_` DI globals (patchované testy, ne typová bezpečnost)
- Nested `async def run()` metoda vrací 14-prvkový tuple místo strukturovaného objektu
- `PipelineRunResult` má 200+ polí — porušení SRP
- `PipelinePageResult` má 25 polí — porušení SRP

### 2. Dva oddělené pipeline lifecycles

`live_public_pipeline` a `live_feed_pipeline` jsou **paralelní implementace** stejného patternu, ne dvě instance společného stage graphu:

```
live_public_pipeline          live_feed_pipeline
├── discovery                 ├── feed fetch
├── fetch+extract            ├── entry normalization
├── pattern match            ├── text assembly
├── quality gate             ├── pattern scan
├── finding build            ├── dedup
└── store                   └── store
```

Společný pouze:
- `FindingPipeline` (enrich→store) — sdílený
- `DuckDBShadowStore.async_ingest_findings_batch()` — kanonická write path
- `CanonicalFinding` — sdílený output typ

### 3. Rust pipeline_compose EXISTUJE ale NENÍ VYUŽIT pro CPU stages

`core/rust_backend/pipeline_compose.py` poskytuje:
```python
pipeline_map()        # MAP stage (len, lower, hash_xxh3, ...)
pipeline_filter()     # FILTER stage
pipeline_fold()      # FOLD stage
pipeline_count()     # COUNT stage
pipeline_batch_stats()  # batch statistics
```

Použito pouze v `sidecar_bus.py` pro sidecar event processing. **Pro hlavní pipeline se nepoužívá** — Python async Queue + dict soup místo toho.

### 4. Data flow není SoA, je AoS dict soup

Mezi stage se předávají **Python objekty s dictionariestrukturou**:
```python
result = await _fetch_and_process_page(...)  # PipelinePageResult (msgspec.Struct)
finding = await _build_public_finding(...)   # CanonicalFinding
```

`PipelinePageResult` má 25 polí, `CanonicalFinding` má 40+ polí.

**SoA design by vypadal:**
```python
class PageBatch(msgspec.Struct, frozen=True, gc=False):
    urls: list[str]
    titles: list[str]
    texts: list[str]           # extracted page text
    matched_patterns: list[int]
    quality_signals: list[float]
```

### 5. Export je OOP třída místo Stage

`export_manager.py` — `ExportManager` je volán na KONCI pipeline:
```python
export_mgr = get_export_manager(_resolved_export_dir)
md_path = export_mgr.export_markdown(report=report, ...)
```

Export je **monolitický post-processing** namísto **pipeline stage**.

---

## Architektura navrhovaného řešení

### Stage Graph Pattern

```
┌─────────────────────────────────────────────────────────────────┐
│                      STAGE ORCHESTRATOR                          │
│  (pipeline/_stage_graph.py — Python async, žádné stage logiky)   │
└─────────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
┌──────────────┐    ┌──────────────────┐   ┌──────────────┐
│  CPU Stage 1 │    │  CPU Stage 2    │   │  CPU Stage 3 │
│  (Rust/rayon)│    │  (Rust/rayon)   │   │  (Rust/rayon)│
│  SoA batch   │    │  SoA batch      │   │  SoA batch   │
└──────────────┘    └──────────────────┘   └──────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
┌──────────────────────────────────────────────────────────────┐
│              GPU/MLX Stage (Hermes3 Engine)                  │
│              Zero-copy memoryview z CPU stages                │
└──────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│              IO Stage (DuckDB + LMDB + Graph)                  │
│              async write, Arrow IPC, zero-copy                │
└──────────────────────────────────────────────────────────────┘
```

### Nová struktura

```
pipeline/
├── __init__.py
├── _stage_graph.py              # NEW: StageOrchestrator, Stage, StageResult
├── _soa_types.py                # NEW: msgspec Structs pro SoA batches
├── _rust_stages.py              # NEW: Rust pipeline_compose wrappers
├── public/
│   ├── __init__.py              # re-exports
│   ├── _discovery_stage.py       # EXTRACT: generate_*_urls, discovery search
│   ├── _fetch_stage.py          # MOVE: _fetch_and_process_page (from public_fetch.py)
│   ├── _extract_stage.py        # EXTRACT: _score_page_quality, _compute_page_usable_fields
│   ├── _match_stage.py          # EXTRACT: PatternMatcher dispatch
│   ├── _build_stage.py          # EXTRACT: _build_public_finding
│   └── _export_stage.py         # MOVE: export_manager wiring
├── feed/
│   ├── __init__.py
│   ├── _fetch_feed_stage.py     # EXTRACT: feed fetch + parse
│   ├── _assemble_stage.py        # EXTRACT: text assembly from scoring.py
│   ├── _scan_stage.py           # EXTRACT: pattern scan
│   └── _dedup_stage.py          # EXTRACT: dedup logic
├── live_public_pipeline.py       # REFACTOR: composes stages, backwards-compat re-export
├── live_feed_pipeline.py        # REFACTOR: composes stages, backwards-compat re-export
└── finding_pipeline.py          # KEEP: enrich→store queue (works well)
```

---

## Migrace Varianta A: Inkrementální (doporučeno)

1. **Týden 1:** Rozdělit `live_public_pipeline.py` na `public/` balíček (importuje z původního souboru)
2. **Týden 2:** Vytvořit `_stage_graph.py` a `StageOrchestrator`
3. **Týden 3:** Převést CPU-bound stages na Rust pipeline_compose
4. **Týden 4:** Převést data flow na SoA batches

**Výhody:** Minimální risk, testy po každém kroku
**Nevýhody:** Delší doba, dvě verze API během migrace

---

## Související existující práce

| Modul | Status | Relevance |
|-------|--------|-----------|
| `core/rust_backend/pipeline_compose.py` | EXISTUJE | Rust MAP/FILTER/FOLD CPU stages |
| `core/rust_backend/feed_pipeline.py` | EXISTUJE | Rust Aho-Corasick feed scan |
| `pipeline/finding_pipeline.py` | OPRAVENÝ | enrich→store queue funguje dobře |
| `pipeline/public_fetch.py` | EXTRAHOVANÝ | fetch fáze už oddělena (F290) |
| `export/export_manager.py` | EXISTUJE | Export Manager je hotový |

---

## Odhadovaný effort

| Fáze | Řádků nových | Řádků změněných | Risk |
|------|-------------|-----------------|------|
| Stage graph framework | ~400 | 0 | NÍZKÝ |
| Public pipeline dekompozice | ~1500 | ~500 | STŘEDNÍ |
| Feed pipeline dekompozice | ~1200 | ~300 | STŘEDNÍ |
| SoA batch types | ~200 | ~1000 | VYSOKÝ (API break) |
| Export stage | ~150 | ~100 | NÍZKÝ |

**Celkem:** ~3450 nových/změněných řádků, 3-4 týdny inkrementální migrace.

---

## Klíčová rozhodnutí k diskusi

1. **SoA vs AoS:** Přechod na Arrow/SoA batch mezi stages — vyžaduje změnu `CanonicalFinding` na `CanonicalFindingBatch`?
2. **Rust pipeline_compose vs Python async:** Které stages přesně migrovat na Rust? (discovery URL generation = Python-only, text extraction = Rust candidate)
3. **Export jako stage:** ExportManager jako preprocessing nebo post-processing?
4. **Backward compatibility:** `live_public_pipeline.py` zůstává jako re-export pro existující callers, nebo se mění API?

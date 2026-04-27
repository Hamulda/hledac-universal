# LONGTERM_PLAN

## Audit Update 2026-04-24

Aktualizace po reálném auditu repozitáře dne 2026-04-24. Audit četl skutečné soubory v `universal/`, ne historické předpoklady:

- Truth docs: `REAL_ARCHITECTURE.md`, `GHOST_INVARIANTS.md`, `STORAGE_LAYER_DOCUMENTATION.md`, `AGENTS.md`.
- Canonical runtime: `core/__main__.py`, `runtime/sprint_scheduler.py`, `pipeline/live_feed_pipeline.py`, `pipeline/live_public_pipeline.py`, `knowledge/duckdb_store.py`.
- Integrační moduly: `deep_probe.py`, `deep_research/probe_runner.py`, `semantic_deduplicator.py`, `embedding_pipeline.py`, `forensics/enrichment_service.py`, `multimodal/analyzer.py`, `knowledge/ann_index.py`, `prefetch/prefetch_oracle_integration.py`, `monitoring/sprint_dashboard.py`.
- Probe lanes: `tests/probe_f196a/` až `tests/probe_f200c/`.

### Audit verdict

- `pytest tests/probe_f196a tests/probe_f196b tests/probe_f197a tests/probe_f197b tests/probe_f197c tests/probe_f198a tests/probe_f198b tests/probe_f198c tests/probe_f199a tests/probe_f199b tests/probe_f200a tests/probe_f200b tests/probe_f200c -q` → **307 passed, 1 warning**.
- `python smoke_runner.py --smoke` → **FAILED** on stale `AdaptiveSemaphore` / `FETCH_SEMAPHORE` expectations:
  - `AdaptiveSemaphore.__init__()` does not accept `initial_value`.
  - root `FETCH_SEMAPHORE` is `_FetchSemaphoreProxy`, not `AdaptiveSemaphore`.
  - smoke expects `current_limit` on a plain `asyncio.Semaphore`.
- Original phases `F196A` through `F200C` have implementation commits and passing probe lanes, but historical commits did not preserve the intended one-phase-one-commit convention. Evidence commits:
  - `9afd1b4 feat: sprint F200C integration — async batch pipeline, ANN fast path, prefetch oracle`
  - `e40035d feat: sprint F200D integration — ghost module cleanup, semantic dedup, stealth, prefetch`
  - `2e0f252 fix: sprint F200D pre-integration — async patterns, scheduler bounds, optimizations`
- `F200D` work exists outside this original roadmap: ghost cleanup hardening, async-pattern fixes, scheduler bounds, JARM/hypothesis optimizations and probe lane `tests/probe_f196c/` → **8 passed**.
- New debt found by the audit was tracked below as `F201A` through `F201C`; strategic audit 2026-04-24 verified those lanes as done enough for Phase 2 planning (`tests/probe_f201a`-`f201c` green and smoke green).

### Changes since 2026-04-23 baseline

- New or newly-active files: `knowledge/ann_index.py`, `prefetch/prefetch_oracle_integration.py`, `monitoring/sprint_dashboard.py`, `tests/probe_f196a/` through `tests/probe_f200c/`, `tests/probe_f196c/`.
- Deleted files in current git history: `runtime/intelligence_dispatcher.py`, `runtime/memory_watchdog.py`, `runtime/session_authority.py`, `rl/marl_coordinator.py`; backup variants for deleted runtime ghosts are also gone in history, while old bytecode artifacts may still exist locally.
- Added dependencies in `requirements.txt`: `probables>=4.0.0`, `httpx>=0.27.0`, `pyzipper>=0.4.0`.
- Current untracked/local artifacts observed but not part of this roadmap commit: `.backup/`, `.codebase-memory/`, `.full-review/`, `rl/.sprint_policy_state.json`, caches and `.DS_Store` files.

## Planning Baseline

This plan is based on the current repo state in `universal/` as of 2026-04-23 and on the following sources:

- `REAL_ARCHITECTURE.md`
- `GHOST_INVARIANTS.md`
- `STORAGE_LAYER_DOCUMENTATION.md`
- `project_types.py`
- `AGENTS.md`

### Hard assumptions carried through all phases

- M1 Air 8 GB UMA is the non-negotiable target.
- Canonical persistent finding write seam remains `knowledge/duckdb_store.py` via `insert_shadow_finding()` internals and the public canonical batch path `async_ingest_findings_batch()`.
- DHT findings remain no-persist unless explicitly re-speced later.
- `brain/model_lifecycle.py` remains the only allowed model load/unload authority.
- `live_feed_pipeline.py` tuple contract stays frozen at 15 items unless every unpack site is updated in the same phase.
- Every external call must have timeout + graceful fallback.
- `REAL_ARCHITECTURE.md` must be updated at the end of every phase.
- One phase = one commit: `feat: sprint FXXX — [popis]`.

### Recommended phase order

1. P0 stabilization first: remove dead authority surfaces, align docs, add missing probe coverage.
2. P1 integration second: wire dormant-but-real modules into the canonical path without violating M1 memory rules.
3. P2 capabilities third: enrich findings with forensics and multimodal data, then add operator UX.
4. P3 performance last: optimize only after canonical flow and persistence are settled.

---

## [DONE] F196A — Canonical Baseline And Ghost Verdict

**Audit 2026-04-24:** implemented in current repo. Probe lane `tests/probe_f196a/` passed as part of the 307-test run. `runtime/telemetry.py` is correctly retained as active; `runtime/intelligence_dispatcher.py`, `runtime/memory_watchdog.py`, `runtime/session_authority.py` and `rl/marl_coordinator.py` are deleted from source.

### 1. Název a cíl fáze

Stabilizovat architektonickou pravdu po F195C, odstranit ghost authority surfaces a rozhodnout delete vs keep pro `rl/marl_coordinator.py`.

### 2. Konkrétní soubory k vytvoření/upravení

- `REAL_ARCHITECTURE.md` — doplnit F195C reality: `forensics/`, `multimodal/`, `graph/quantum_pathfinder.py`, DeepProbe post-sprint lane, current RL reality.
- `runtime/intelligence_dispatcher.py` — delete if still 0 canonical call-sites after audit.
- `runtime/memory_watchdog.py` — delete if still 0 canonical call-sites after audit.
- `runtime/session_authority.py` — delete if still 0 canonical call-sites after audit.
- `runtime/telemetry.py` — delete if still 0 canonical call-sites after audit.
- `rl/marl_coordinator.py` — delete stub path; preserve or redirect any legitimate functionality to `rl/sprint_policy_manager.py`.
- `rl/__init__.py` — remove deleted exports.
- `tests/probe_f196a/test_canonical_baseline_and_ghost_verdict.py` — architecture/probe assertions.

### 3. Definition of Done

- No deleted ghost module has canonical call-sites in `core/__main__.py`, `runtime/sprint_scheduler.py`, `pipeline/live_*`, `knowledge/duckdb_store.py`.
- No code outside deleted modules imports them, except allowed compatibility tests updated in the same phase.
- `rl/marl_coordinator.py` is either gone or explicitly documented as retained for a real caller.
- `REAL_ARCHITECTURE.md` matches repo reality after cleanup.
- `pytest tests/probe_f196a/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### 4. Probe testy

- `tests/probe_f196a/test_canonical_baseline_and_ghost_verdict.py`

### 5. Claude Code prompt pro tuto fázi

```text
[FÁZE F196A — CANONICAL BASELINE AND GHOST VERDICT]

Kontext:

Canonical sprint owner je core/__main__.py::run_sprint().
Canonical persistent finding write seam je knowledge/duckdb_store.py::async_ingest_findings_batch().
REAL_ARCHITECTURE.md je truth doc, ne ARCHITECTURE_MAP.py.
Ghost kandidáti bez canonical call-sites: runtime/intelligence_dispatcher.py, runtime/memory_watchdog.py, runtime/session_authority.py, runtime/telemetry.py.
rl/marl_coordinator.py je dnes stub/experiment, zatímco rl/sprint_policy_manager.py má reálnější reward contract.

Na co navazuješ:

Na sprint F195C a na debt cleanup priority P0.

Zadání:

1. Proveď call-site audit ghost modulů vůči canonical sprint path.
2. Ghost moduly bez reálných call-sites smaž, nevytvářej nové faux wiring.
3. Rozhodni delete vs keep pro rl/marl_coordinator.py; preferuj delete, pokud nenajdeš reálného calleru.
4. Aktualizuj REAL_ARCHITECTURE.md tak, aby popisoval jen skutečně active a dormant surfaces.
5. Přidej probe testy, které hlídají, že ghost authority surfaces nejsou znovu zavedeny.

Soubory k úpravě/vytvoření:

REAL_ARCHITECTURE.md — aktualizace canonical reality po cleanupu
runtime/intelligence_dispatcher.py — delete pokud bez call-sites
runtime/memory_watchdog.py — delete pokud bez call-sites
runtime/session_authority.py — delete pokud bez call-sites
runtime/telemetry.py — delete pokud bez call-sites
rl/marl_coordinator.py — delete nebo explicit verdict
rl/__init__.py — cleanup exportů
tests/probe_f196a/test_canonical_baseline_and_ghost_verdict.py — nové probe testy

Constraints:

- Nepřepisuj ghost moduly jako nový vzor; buď delete, nebo doložené wire-up.
- Žádné nové public APIs bez nutnosti.
- Žádné absolutní cesty mimo paths.py.
- Každý závěr musí sedět s reálnými call-sites, ne jen s importy.

Definition of Done:

pytest tests/probe_f196a/ -q → všechny pass
python smoke_runner.py --smoke → OK
REAL_ARCHITECTURE.md odpovídá skutečnosti po cleanupu
žádný ghost modul není importován z canonical sprint path

Commit message formát: "feat: sprint F196A — canonical baseline and ghost verdict"
```

---

## [DONE] F196B — Probe Coverage For Forensics And Multimodal

**Audit 2026-04-24:** implemented in current repo. Probe lane `tests/probe_f196b/` passed as part of the 307-test run. This lane also grew extra hardening tests for async correctness, memory bounds and security behavior.

### 1. Název a cíl fáze

Zavést probe coverage pro F195C moduly, které už existují, ale nemají vlastní probe lane.

### 2. Konkrétní soubory k vytvoření/upravení

- `tests/probe_f196b/test_forensics_probe_lane.py`
- `tests/probe_f196b/test_multimodal_probe_lane.py`
- `forensics/enrichment_service.py` — jen pokud probe odhalí contract drift.
- `multimodal/analyzer.py` — jen pokud probe odhalí contract drift.
- `runtime/sprint_scheduler.py` — jen pokud probe odhalí mismatch v lifecycle hooks/counters.
- `REAL_ARCHITECTURE.md` — doplnit probe coverage status.

### 3. Definition of Done

- Forensics probe lane ověřuje init/close fail-soft, supported-file gating a scheduler hook contract.
- Multimodal probe lane ověřuje init/close fail-soft, supported-file gating a scheduler hook contract.
- Neexistuje reliance jen na staré `tests/test_forensics_enrichment.py` a `tests/test_multimodal_analyzer.py`; probe family je nová SSOT pro F195C.
- `pytest tests/probe_f196b/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### 4. Probe testy

- `tests/probe_f196b/test_forensics_probe_lane.py`
- `tests/probe_f196b/test_multimodal_probe_lane.py`

### 5. Claude Code prompt pro tuto fázi

```text
[FÁZE F196B — PROBE COVERAGE FOR FORENSICS AND MULTIMODAL]

Kontext:

forensics/enrichment_service.py a multimodal/analyzer.py už existují.
Mají běžné testy, ale chybí jim probe lane ve stylu sprint probe family.
Scheduler už obsahuje enrichment counters a lifecycle seam, ale coverage je mimo probe naming.

Na co navazuješ:

Na F196A cleanup a na F195C additions.

Zadání:

1. Vytvoř probe test family pro forensics a multimodal.
2. Probe testy mají zamknout fail-soft behavior, lifecycle init/flush/close, supported-file gating a counter contracts.
3. Oprav jen minimální drift v implementaci, pokud probe odhalí rozpadlý contract.
4. Aktualizuj REAL_ARCHITECTURE.md o tom, že F195C už má probe coverage.

Soubory k úpravě/vytvoření:

tests/probe_f196b/test_forensics_probe_lane.py — nové probe testy
tests/probe_f196b/test_multimodal_probe_lane.py — nové probe testy
forensics/enrichment_service.py — pouze contract fixy nutné pro probe
multimodal/analyzer.py — pouze contract fixy nutné pro probe
runtime/sprint_scheduler.py — pouze pokud probe odhalí hook drift
REAL_ARCHITECTURE.md — update coverage section

Constraints:

- Fail-soft everywhere.
- Žádné rozšíření functionality mimo coverage + contract fix.
- Nezavádět nový storage path; enrichment je stále additive sidecar.

Definition of Done:

pytest tests/probe_f196b/ -q → všechny pass
python smoke_runner.py --smoke → OK
probe lane existuje pro forensics i multimodal

Commit message formát: "feat: sprint F196B — add probe coverage for forensics and multimodal"
```

---

## [DONE] F197A — DeepProbe Canonical Ingest

**Audit 2026-04-24:** implemented in current repo. Probe lane `tests/probe_f197a/` passed as part of the 307-test run. `deep_research/probe_runner.py` now documents and calls `async_ingest_findings_batch()`.

### 1. Název a cíl fáze

Převést DeepProbe z post-sprint side activity na canonical finding producer, který perzistuje přes `async_ingest_findings_batch()`.

### 2. Konkrétní soubory k vytvoření/upravení

- `deep_probe.py` — standardizovat output na `CanonicalFinding` batch nebo producer DTO.
- `deep_research/probe_runner.py` — odstranit starý/nesprávný write-path popis a volat canonical batch ingest.
- `knowledge/duckdb_store.py` — jen pokud je potřeba tenký adapter pro probe findings.
- `core/__main__.py` — truth/accounting po DeepProbe ingestu.
- `project_types.py` — jen pokud chybí typ pro bounded probe summary.
- `tests/probe_f197a/test_deep_probe_canonical_ingest.py`
- `REAL_ARCHITECTURE.md`

### 3. Definition of Done

- DeepProbe findings jdou pouze přes `async_ingest_findings_batch()`.
- `deep_probe` findings mají `source_type="deep_probe"`.
- Post-sprint run je stále bounded a fail-soft.
- Export/smoke path není blokován DeepProbe failure.
- `pytest tests/probe_f197a/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### 4. Probe testy

- `tests/probe_f197a/test_deep_probe_canonical_ingest.py`

### 5. Claude Code prompt pro tuto fázi

```text
[FÁZE F197A — DEEPPROBE CANONICAL INGEST]

Kontext:

deep_research/probe_runner.py dnes běží post-sprint a dokumentace uvnitř souboru ještě odkazuje na starý write seam.
Canonical write path je knowledge/duckdb_store.py::async_ingest_findings_batch().
DeepProbe je priorita P1: findings se mají perzistovat, ale bez porušení bounded/fail-soft invariantů.

Na co navazuješ:

Na F196A/F196B stabilizaci a probe baseline.

Zadání:

1. Uprav DeepProbe flow tak, aby výsledky končily jako CanonicalFinding batch.
2. Veškerá persistence musí jít přes async_ingest_findings_batch().
3. Zachovej post-export, bounded, fail-soft charakter DeepProbe.
4. DHT findings nepersistuj.
5. Aktualizuj runtime truth/counters tak, aby DeepProbe bylo auditovatelné, ale nezkreslovalo feed/public invariants.

Soubory k úpravě/vytvoření:

deep_probe.py — findings normalization
deep_research/probe_runner.py — canonical ingest wiring
knowledge/duckdb_store.py — případný adapter/helper
core/__main__.py — truth/counter integration
project_types.py — pouze pokud je potřeba typed summary
tests/probe_f197a/test_deep_probe_canonical_ingest.py — nové probe testy
REAL_ARCHITECTURE.md — update canonical path section

Constraints:

- Žádné přímé volání _sync_insert_finding mimo duckdb_store.py.
- DeepProbe failure nesmí zablokovat sprint export.
- External API calls mají timeout + graceful fallback.

Definition of Done:

pytest tests/probe_f197a/ -q → všechny pass
python smoke_runner.py --smoke → OK
DeepProbe findings jsou perzistované jen přes async_ingest_findings_batch()

Commit message formát: "feat: sprint F197A — wire DeepProbe into canonical ingest"
```

---

## [DONE] F197B — Semantic Dedup At Canonical Write Seam

**Audit 2026-04-24:** implemented in current repo. Probe lane `tests/probe_f197b/` passed as part of the 307-test run. `DuckDBShadowStore._assess_finding_quality()` contains the fail-open semantic dedup hook.

### 1. Název a cíl fáze

Zapojit `semantic_deduplicator.py` přímo do `duckdb_store.py` před insert, bez porušení fail-open a UMA budgetu.

### 2. Konkrétní soubory k vytvoření/upravení

- `knowledge/duckdb_store.py`
- `semantic_deduplicator.py`
- `embedding_pipeline.py` — jen pokud je nutný memory/lifecycle contract fix.
- `tests/probe_f197b/test_semantic_dedup_write_seam.py`
- `REAL_ARCHITECTURE.md`

### 3. Definition of Done

- Semantic dedup se spouští na canonical write seam před accept/reject finalization.
- Při low-memory nebo embedder failure se systém otevře fail-open a finding se dál posoudí standardně.
- Nepřibude žádná nová persistence authority mimo LMDB cache uvnitř semantic dedup.
- `pytest tests/probe_f197b/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### 4. Probe testy

- `tests/probe_f197b/test_semantic_dedup_write_seam.py`

### 5. Claude Code prompt pro tuto fázi

```text
[FÁZE F197B — SEMANTIC DEDUP AT CANONICAL WRITE SEAM]

Kontext:

semantic_deduplicator.py už existuje a dokumentuje, že se má volat z DuckDBShadowStore._assess_finding_quality().
Canonical write seam je duckdb_store.py.
M1 Air 8 GB vyžaduje fail-open behavior při memory pressure.

Na co navazuješ:

Na F197A canonical ingest rozšíření.

Zadání:

1. Zapoj semantic_deduplicator.py do canonical quality gate v duckdb_store.py.
2. Deduplikace musí běžet po hash/url dedup a před finálním accept/reject rozhodnutím.
3. Při low-memory, LMDB boot failure nebo embedder failure nesmí být finding zahozen jen kvůli chybě dedupu.
4. Přidej probe testy, které zamknou hook pořadí a fail-open semantics.

Soubory k úpravě/vytvoření:

knowledge/duckdb_store.py — semantic dedup hook
semantic_deduplicator.py — contract fixy podle probe
embedding_pipeline.py — pouze lifecycle/memory fix pokud nutné
tests/probe_f197b/test_semantic_dedup_write_seam.py — nové probe testy
REAL_ARCHITECTURE.md — update storage path section

Constraints:

- Jediný canonical write path zůstává v duckdb_store.py.
- Model lifecycle vždy přes brain/model_lifecycle.py.
- Žádný JS renderer současně s načteným LLM.

Definition of Done:

pytest tests/probe_f197b/ -q → všechny pass
python smoke_runner.py --smoke → OK
semantic dedup je volán na canonical write seam a je fail-open

Commit message formát: "feat: sprint F197B — add semantic dedup to canonical write seam"
```

---

## [DONE] F197C — Embedding Pipeline Wiring In Public Pipeline

**Audit 2026-04-24:** implemented in current repo. Probe lane `tests/probe_f197c/` passed as part of the 307-test run. `pipeline/live_public_pipeline.py` has embedding sidecar wiring and renderer/model guard checks.

### 1. Název a cíl fáze

Napojit `embedding_pipeline.py` do `live_public_pipeline.py` tak, aby vznikal canonical semantic sidecar bez kolize s JS renderingem.

### 2. Konkrétní soubory k vytvoření/upravení

- `pipeline/live_public_pipeline.py`
- `embedding_pipeline.py`
- `brain/model_lifecycle.py` — jen pokud chybí explicitní guard/helper.
- `fetching/public_fetcher.py` — případný explicitní guard mezi embedder/LLM a Camoufox/nodriver.
- `tests/probe_f197c/test_public_pipeline_embedding_wiring.py`
- `REAL_ARCHITECTURE.md`

### 3. Definition of Done

- Public pipeline umí emitnout embedding side-effects pro accepted findings.
- Před Camoufox/nodriver je zajištěno, že není držen LLM/embedder v konfliktu s UMA budgetem.
- `FETCH_SEMAPHORE` respektuje limit 3 při loaded model path.
- `pytest tests/probe_f197c/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### 4. Probe testy

- `tests/probe_f197c/test_public_pipeline_embedding_wiring.py`

### 5. Claude Code prompt pro tuto fázi

```text
[FÁZE F197C — EMBEDDING PIPELINE WIRING IN PUBLIC PIPELINE]

Kontext:

embedding_pipeline.py existuje, ale canonical sprint path jej zatím nevolá.
live_public_pipeline.py je nejhustší integrační plocha.
Na M1 Air 8 GB nesmí běžet model + JS renderer současně.

Na co navazuješ:

Na F197B semantic dedup hook a na hotový canonical ingest.

Zadání:

1. Napoj embedding pipeline do live_public_pipeline.py pro accepted findings.
2. Zachovej fail-soft behavior: embedding failure nesmí rozbít pipeline.
3. Vynucuj model load/unload přes brain/model_lifecycle.py.
4. Ošetři memory guard vůči Camoufox/nodriver a FETCH_SEMAPHORE limitu.
5. Přidej probe testy pro ordering a memory discipline.

Soubory k úpravě/vytvoření:

pipeline/live_public_pipeline.py — embedding hook
embedding_pipeline.py — contract/lifecycle fixy
brain/model_lifecycle.py — helper pokud nutný
fetching/public_fetcher.py — render guard pokud nutný
tests/probe_f197c/test_public_pipeline_embedding_wiring.py — nové probe testy
REAL_ARCHITECTURE.md — update public pipeline section

Constraints:

- Nikdy nenačítej model + JS renderer současně.
- FETCH_SEMAPHORE limit=3 při loaded LLM path.
- Fail-soft everywhere.

Definition of Done:

pytest tests/probe_f197c/ -q → všechny pass
python smoke_runner.py --smoke → OK
embedding sidecar je wired do public pipeline bez porušení UMA guardů

Commit message formát: "feat: sprint F197C — wire embedding pipeline into public pipeline"
```

---

## [DONE] F198A — Cross-Sprint Graph Accumulation

**Audit 2026-04-24:** implemented in current repo. Probe lane `tests/probe_f198a/` passed as part of the 307-test run. `runtime/sprint_scheduler.py` has `_accumulate_findings_to_graph()` and fail-soft graph signal reads.

### 1. Název a cíl fáze

Napojit `graph/quantum_pathfinder.py` a graph memory seam na cross-sprint accumulation a read-side pivots.

### 2. Konkrétní soubory k vytvoření/upravení

- `graph/quantum_pathfinder.py`
- `knowledge/graph_service.py`
- `runtime/sprint_scheduler.py`
- `knowledge/duckdb_store.py` — pokud je potřeba read helper pro graph donor data.
- `tests/probe_f198a/test_cross_sprint_graph_accumulation.py`
- `REAL_ARCHITECTURE.md`

### 3. Definition of Done

- Accepted findings produkují idempotent graph upserts do cross-sprint seam.
- Scheduler umí během windup/exportu načíst jednoduchý graph summary bez pádu sprintu.
- `quantum_pathfinder` zůstává donor/analytics overlay, ne write authority.
- `pytest tests/probe_f198a/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### 4. Probe testy

- `tests/probe_f198a/test_cross_sprint_graph_accumulation.py`

### 5. Claude Code prompt pro tuto fázi

```text
[FÁZE F198A — CROSS-SPRINT GRAPH ACCUMULATION]

Kontext:

graph/quantum_pathfinder.py je dnes analytics overlay.
STORAGE_LAYER_DOCUMENTATION.md říká, že graph_service.py je sprint memory seam pro cross-sprint persistence.
Priorita P1 vyžaduje knowledge accumulation mezi sprinty.

Na co navazuješ:

Na F197C, kdy už canonical findings tečou i přes semantic sidecars.

Zadání:

1. Připoj accepted findings k graph_service seam idempotentně.
2. Udrž quantum_pathfinder jako read-side/analytics overlay, ne jako truth store.
3. Přidej scheduler/export summary hook, který umí z grafu číst signal, ale nikdy neblokuje sprint.
4. Přidej probe testy pro idempotentní accumulation a fail-soft graph failure.

Soubory k úpravě/vytvoření:

graph/quantum_pathfinder.py — read-side helper/pivot usage
knowledge/graph_service.py — upsert/read seam
runtime/sprint_scheduler.py — accumulation + summary hook
knowledge/duckdb_store.py — případný donor helper
tests/probe_f198a/test_cross_sprint_graph_accumulation.py — nové probe testy
REAL_ARCHITECTURE.md — graph section update

Constraints:

- Graph service není canonical finding write path.
- Fail-soft: graph failure nesmí zastavit sprint.
- Žádný import :memory:, žádné absolutní cesty mimo paths.py.

Definition of Done:

pytest tests/probe_f198a/ -q → všechny pass
python smoke_runner.py --smoke → OK
accepted findings se propisují do cross-sprint graph seam idempotentně

Commit message formát: "feat: sprint F198A — add cross-sprint graph accumulation"
```

---

## [DONE] F198B — Forensics Metadata On Canonical Findings

**Audit 2026-04-24:** implemented in current repo. Probe lane `tests/probe_f198b/` passed as part of the 307-test run. `forensics/enrichment_service.py` exposes typed `ForensicsResult` and injects `finding.metadata["forensics"]`.

### 1. Název a cíl fáze

Převést forensics z LMDB-only sidecar na canonical finding enrichment: `ForensicsEnricher.enrich(CanonicalFinding)` zapisuje výsledek do `finding.metadata["forensics"]`.

### 2. Konkrétní soubory k vytvoření/upravení

- `forensics/enrichment_service.py`
- `project_types.py` — typed result/dataclass aliases pokud chybí.
- `pipeline/live_public_pipeline.py` a/nebo `runtime/sprint_scheduler.py` — canonical injection point.
- `knowledge/duckdb_store.py` — persistence of enriched metadata if needed.
- `tests/probe_f198b/test_forensics_metadata_enrichment.py`
- `REAL_ARCHITECTURE.md`

### 3. Definition of Done

- `ForensicsEnricher.enrich()` vrací strukturovaný `ForensicsResult`.
- Výsledek se propíše do `CanonicalFinding.metadata["forensics"]`.
- WHOIS/SSL/DNS/rDNS calls mají timeout a graceful fallback.
- Fail-soft enrichment nikdy nesmí zablokovat ingest.
- `pytest tests/probe_f198b/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### 4. Probe testy

- `tests/probe_f198b/test_forensics_metadata_enrichment.py`

### 5. Claude Code prompt pro tuto fázi

```text
[FÁZE F198B — FORENSICS METADATA ON CANONICAL FINDINGS]

Kontext:

forensics/enrichment_service.py už existuje, ale dnes funguje spíš jako sidecar enrichment.
Priorita P2 chce ForensicsEnricher.enrich(CanonicalFinding) → WHOIS/SSL/DNS/rDNS → ForensicsResult v finding.metadata.

Na co navazuješ:

Na F196B probe coverage a na canonical ingest stabilizovaný v F197x.

Zadání:

1. Rozšiř forensics enrichment tak, aby produkoval typed ForensicsResult.
2. Napoj enrichment do canonical finding flow.
3. Ulož výsledek do finding.metadata["forensics"], ne do alternativního write path.
4. Každý external lookup musí mít timeout + graceful fallback.
5. Přidej probe testy pro metadata injection a fail-soft behavior.

Soubory k úpravě/vytvoření:

forensics/enrichment_service.py — typed enrichment + external lookups
project_types.py — typed result definitions pokud chybí
pipeline/live_public_pipeline.py a/nebo runtime/sprint_scheduler.py — injection point
knowledge/duckdb_store.py — persistence contract pro metadata
tests/probe_f198b/test_forensics_metadata_enrichment.py — nové probe testy
REAL_ARCHITECTURE.md — update enrichment section

Constraints:

- Žádné nové feature flagy.
- External calls timeout + graceful fallback.
- Ingest zůstává canonical přes duckdb_store.py.

Definition of Done:

pytest tests/probe_f198b/ -q → všechny pass
python smoke_runner.py --smoke → OK
forensics result je přítomen v finding.metadata["forensics"] u enrichovatelných findings

Commit message formát: "feat: sprint F198B — enrich canonical findings with forensics metadata"
```

---

## [DONE] F198C — Multimodal Document Findings

**Audit 2026-04-24:** implemented in current repo. Probe lane `tests/probe_f198c/` passed as part of the 307-test run. `multimodal/analyzer.py` has `DocumentExtractor` producing `CanonicalFinding(source_type="document")`.

### 1. Název a cíl fáze

Napojit `multimodal/` na dokumentový ingest tak, aby PDF/image extraction vytvářel `CanonicalFinding` se `source_type="document"`.

### 2. Konkrétní soubory k vytvoření/upravení

- `multimodal/analyzer.py`
- `multimodal/vision_encoder.py`
- `pipeline/live_public_pipeline.py`
- `project_types.py`
- `knowledge/duckdb_store.py`
- `tests/probe_f198c/test_multimodal_document_findings.py`
- `REAL_ARCHITECTURE.md`

### 3. Definition of Done

- PDF/image sources mohou být převedeny na canonical findings se `source_type="document"`.
- Extracted text/vision evidence je bounded a fail-soft.
- Multimodal path nepřekročí M1 RAM discipline a nepoběží paralelně s nebezpečnou renderer/model kombinací.
- `pytest tests/probe_f198c/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### 4. Probe testy

- `tests/probe_f198c/test_multimodal_document_findings.py`

### 5. Claude Code prompt pro tuto fázi

```text
[FÁZE F198C — MULTIMODAL DOCUMENT FINDINGS]

Kontext:

multimodal/analyzer.py dnes existuje jako enrichment service.
Priorita P2 chce PDF/image extraction, které vytvoří CanonicalFinding se source_type="document".

Na co navazuješ:

Na F198B forensics metadata enrichment a na hotový public pipeline wiring.

Zadání:

1. Přidej document extraction path pro PDF/image inputs.
2. Výstup musí být CanonicalFinding batch se source_type="document".
3. Ulož findings přes canonical duckdb ingest.
4. Udrž bounded memory behavior na M1 a fail-soft fallback.
5. Přidej probe testy pro document findings a source_type contract.

Soubory k úpravě/vytvoření:

multimodal/analyzer.py — document extraction orchestration
multimodal/vision_encoder.py — bounded extraction helpers
pipeline/live_public_pipeline.py — hook pro document findings
project_types.py — typed document finding helpers pokud nutné
knowledge/duckdb_store.py — persistence contract
tests/probe_f198c/test_multimodal_document_findings.py — nové probe testy
REAL_ARCHITECTURE.md — update multimodal section

Constraints:

- source_type musí být přesně "document".
- Fail-soft everywhere.
- Žádné model+renderer overlap na UMA.

Definition of Done:

pytest tests/probe_f198c/ -q → všechny pass
python smoke_runner.py --smoke → OK
PDF/image path vytváří a ukládá CanonicalFinding(source_type="document")

Commit message formát: "feat: sprint F198C — add multimodal document findings"
```

---

## [DONE] F199A — Real Reward Loop In Scheduler

**Audit 2026-04-24:** implemented in current repo. Probe lane `tests/probe_f199a/` passed as part of the 307-test run. Scheduler source weights adapt from `_source_quality_feedback`.

### 1. Název a cíl fáze

Zavést reálnou RL feedback smyčku z `FindingQualityDecision.accepted` do source weights ve `runtime/sprint_scheduler.py`.

### 2. Konkrétní soubory k vytvoření/upravení

- `runtime/sprint_scheduler.py`
- `rl/sprint_policy_manager.py`
- `rl/actions.py`
- `rl/state_extractor.py`
- `tests/probe_f199a/test_reward_loop_source_weights.py`
- `REAL_ARCHITECTURE.md`

### 3. Definition of Done

- Reward se počítá z reálných acceptance outcomes, ne jen z coarse sprint summary.
- Scheduler source weights se adaptují bounded a auditovatelně.
- Pokud RL helper selže, scheduler pokračuje se standard weights.
- `pytest tests/probe_f199a/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### 4. Probe testy

- `tests/probe_f199a/test_reward_loop_source_weights.py`

### 5. Claude Code prompt pro tuto fázi

```text
[FÁZE F199A — REAL REWARD LOOP IN SCHEDULER]

Kontext:

rl/sprint_policy_manager.py už umí základní reward z výsledku sprintu.
Priorita P2 chce reálný reward signal z FindingQualityDecision.accepted → update source weights v sprint_scheduler.
marl_coordinator.py byl v P0 buď smazán, nebo vyřazen z authority.

Na co navazuješ:

Na F198A/F198B/F198C, kdy už pipeline produkuje bohatší acceptance a metadata signal.

Zadání:

1. Napoj granular reward z FindingQualityDecision.accepted do scheduler source weighting.
2. Udrž update bounded, vysvětlitelný a fail-soft.
3. Preferuj evoluci sprint_policy_manager.py místo resurrection marl_coordinator.py.
4. Přidej probe testy pro weight update, fallback a persistence state.

Soubory k úpravě/vytvoření:

runtime/sprint_scheduler.py — source weight adaptation
rl/sprint_policy_manager.py — reward logic z FindingQualityDecision
rl/actions.py — action mapping pokud nutné
rl/state_extractor.py — scheduler state features pokud nutné
tests/probe_f199a/test_reward_loop_source_weights.py — nové probe testy
REAL_ARCHITECTURE.md — update RL section

Constraints:

- Žádná nová fake RL smyčka.
- Scheduler musí fungovat i při chybě RL helperu.
- Fail-soft everywhere.

Definition of Done:

pytest tests/probe_f199a/ -q → všechny pass
python smoke_runner.py --smoke → OK
source weights se adaptují podle reálného accepted signal

Commit message formát: "feat: sprint F199A — add reward-driven source weighting"
```

---

## [DONE] F199B — Terminal Dashboard

**Audit 2026-04-24:** implemented in current repo. Probe lane `tests/probe_f199b/` passed as part of the 307-test run. Dashboard lives at `monitoring/sprint_dashboard.py`, not `runtime/sprint_dashboard.py`.

### 1. Název a cíl fáze

Přidat live textový dashboard pro sprint metrics bez zásahu do canonical execution authority.

### 2. Konkrétní soubory k vytvoření/upravení

- `runtime/sprint_scheduler.py`
- `tools/` nebo nový `runtime/sprint_dashboard.py`
- `core/__main__.py`
- `tests/probe_f199b/test_terminal_dashboard.py`
- `REAL_ARCHITECTURE.md`

### 3. Definition of Done

- Dashboard ukazuje live sprint metrics, branch mix, accepted findings, CT counters a případně enrichment counters.
- UI je optional sidecar, ale bez feature flag sprawl; pokud render selže, sprint pokračuje.
- Žádný dashboard code nepřebírá ownership nad lifecycle.
- `pytest tests/probe_f199b/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### 4. Probe testy

- `tests/probe_f199b/test_terminal_dashboard.py`

### 5. Claude Code prompt pro tuto fázi

```text
[FÁZE F199B — TERMINAL DASHBOARD]

Kontext:

Priorita P2 chce textový terminal UI dashboard.
runtime/sprint_scheduler.py je runtime worker, ne owner; dashboard musí být sidecar observability vrstva.

Na co navazuješ:

Na stabilní counters z předchozích fází.

Zadání:

1. Přidej rich/textual dashboard pro live sprint metrics.
2. Dashboard musí číst existující runtime counters, ne vytvářet druhý source of truth.
3. Render failure musí být fail-soft.
4. Přidej probe testy na data contract a non-blocking behavior.

Soubory k úpravě/vytvoření:

runtime/sprint_dashboard.py — nový dashboard sidecar
runtime/sprint_scheduler.py — feed dat do dashboardu
core/__main__.py — bootstrap napojení
tests/probe_f199b/test_terminal_dashboard.py — nové probe testy
REAL_ARCHITECTURE.md — update observability section

Constraints:

- Dashboard nepřebírá lifecycle authority.
- Žádné nové background ownership patterns mimo existující runtime.
- Fail-soft everywhere.

Definition of Done:

pytest tests/probe_f199b/ -q → všechny pass
python smoke_runner.py --smoke → OK
dashboard běží jako sidecar a sprint dokončí i při UI failure

Commit message formát: "feat: sprint F199B — add live terminal sprint dashboard"
```

---

## [DONE] F200A — Prefetch Oracle

**Audit 2026-04-24:** implemented in current repo. Probe lane `tests/probe_f200a/` passed as part of the 307-test run. The active integration file is `prefetch/prefetch_oracle_integration.py`; `prefetch/prefetch_oracle.py` remains a separate older oracle surface.

### 1. Název a cíl fáze

Převést prefetch oracle z dormant heuristiky na reálnou predikci zdrojů pro další sprint/cycle.

### 2. Konkrétní soubory k vytvoření/upravení

- `prefetch/prefetch_oracle.py`
- `runtime/sprint_scheduler.py`
- `tests/probe_f200a/test_prefetch_oracle_prediction.py`
- `REAL_ARCHITECTURE.md`

### 3. Definition of Done

- Oracle dává bounded doporučení pro prefetch kandidáty.
- Predikce má fallback na current scheduler ordering.
- Žádný oracle failure neblokuje sprint.
- `pytest tests/probe_f200a/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### 4. Probe testy

- `tests/probe_f200a/test_prefetch_oracle_prediction.py`

### 5. Claude Code prompt pro tuto fázi

```text
[FÁZE F200A — PREFETCH ORACLE]

Kontext:

prefetch/prefetch_oracle.py je dnes dormant.
Priorita P3 chce skutečnou predikci, ale až po stabilizaci canonical ingest a scheduler feedback loop.

Na co navazuješ:

Na F199A reward-driven scheduler.

Zadání:

1. Implementuj reálný bounded prefetch oracle.
2. Oracle má jen doporučovat pořadí/prefetch kandidáty; nesmí převzít authority nad schedulerem.
3. Přidej probe testy pro fallback a bounded prediction.

Soubory k úpravě/vytvoření:

prefetch/prefetch_oracle.py — predikční logika
runtime/sprint_scheduler.py — integration hook
tests/probe_f200a/test_prefetch_oracle_prediction.py — nové probe testy
REAL_ARCHITECTURE.md — update performance section

Constraints:

- Fail-soft everywhere.
- Bounded memory and bounded candidate list.
- Žádné nové public APIs bez nutnosti.

Definition of Done:

pytest tests/probe_f200a/ -q → všechny pass
python smoke_runner.py --smoke → OK
oracle doporučuje kandidáty a při failure scheduler padá zpět na stávající ordering

Commit message formát: "feat: sprint F200A — implement bounded prefetch oracle"
```

---

## [DONE] F200B — LanceDB ANN Fast Path

**Audit 2026-04-24:** implemented in current repo. Probe lane `tests/probe_f200b/` passed as part of the 307-test run. Active ANN file is `knowledge/ann_index.py` and the semantic dedup hook calls `check_ann_duplicate()`.

### 1. Název a cíl fáze

Zrychlit semantic dedup/search pod 10 ms přes LanceDB ANN indexaci.

### 2. Konkrétní soubory k vytvoření/upravení

- `semantic_deduplicator.py`
- `knowledge/vector_store.py` a/nebo `knowledge/semantic_store.py`
- `embedding_pipeline.py`
- `tests/probe_f200b/test_lancedb_ann_fastpath.py`
- `REAL_ARCHITECTURE.md`

### 3. Definition of Done

- Semantic dedup/search používá ANN fast path.
- Probe/benchmark lane dokládá sub-10 ms target na representative mocked path.
- Při ANN failure je fallback na current exact/slow path.
- `pytest tests/probe_f200b/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### 4. Probe testy

- `tests/probe_f200b/test_lancedb_ann_fastpath.py`

### 5. Claude Code prompt pro tuto fázi

```text
[FÁZE F200B — LANCEDB ANN FAST PATH]

Kontext:

Priorita P3 chce LanceDB ANN indexing pro semantic dedup < 10ms.
semantic_deduplicator.py a embedding_pipeline.py už budou po F197B/F197C stabilně wired.

Na co navazuješ:

Na hotový semantic dedup hook a embedding sidecar.

Zadání:

1. Přidej ANN fast path pro semantic dedup/search.
2. Zachovej fail-open/fallback behavior.
3. Přidej probe testy a lehký benchmark contract pro sub-10 ms target na representative path.

Soubory k úpravě/vytvoření:

semantic_deduplicator.py — ANN query path
knowledge/vector_store.py a/nebo knowledge/semantic_store.py — index helper
embedding_pipeline.py — dimension/index contract fixy pokud nutné
tests/probe_f200b/test_lancedb_ann_fastpath.py — nové probe testy
REAL_ARCHITECTURE.md — update semantic search section

Constraints:

- Fail-open při ANN init/query failure.
- Žádné porušení M1 memory budgetu.
- Canonical write path zůstává v duckdb_store.py.

Definition of Done:

pytest tests/probe_f200b/ -q → všechny pass
python smoke_runner.py --smoke → OK
ANN fast path je dostupný a fallbackuje na stávající path při chybě

Commit message formát: "feat: sprint F200B — add LanceDB ANN fast path"
```

---

## [DONE] F200C — Async Batch Public Pipeline

**Audit 2026-04-24:** implemented in current repo. Probe lane `tests/probe_f200c/` passed as part of the 307-test run. `python smoke_runner.py --smoke` still fails, but the failure is in smoke/concurrency contract drift, not in the F200C probe lane.

### 1. Název a cíl fáze

Převést sekvenční public branch na bounded async batch processing.

### 2. Konkrétní soubory k vytvoření/upravení

- `pipeline/live_public_pipeline.py`
- `fetching/public_fetcher.py`
- `runtime/sprint_scheduler.py`
- `tests/probe_f200c/test_async_batch_public_pipeline.py`
- `REAL_ARCHITECTURE.md`

### 3. Definition of Done

- Public pipeline zpracovává batch-e paralelně, ale bounded.
- Všechny `asyncio.gather()` používají `return_exceptions=True` a `_check_gathered()`.
- M1 memory discipline a JS/LLM mutual exclusion zůstávají zachované.
- `pytest tests/probe_f200c/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### 4. Probe testy

- `tests/probe_f200c/test_async_batch_public_pipeline.py`

### 5. Claude Code prompt pro tuto fázi

```text
[FÁZE F200C — ASYNC BATCH PUBLIC PIPELINE]

Kontext:

Priorita P3 chce async batch processing v live_public_pipeline, které je dnes převážně sekvenční.
GHOST_INVARIANTS.md lockuje async hygiene: gather(return_exceptions=True) + _check_gathered().

Na co navazuješ:

Na F197C a všechny předchozí integrační fáze.

Zadání:

1. Převáděj public pipeline na bounded async batch processing.
2. Dodrž všechny async hygiene invariants.
3. Zachovej memory guard, fetch semaphore discipline a model/renderer mutual exclusion.
4. Přidej probe testy pro concurrency, fail-soft a gather hygiene.

Soubory k úpravě/vytvoření:

pipeline/live_public_pipeline.py — async batch orchestration
fetching/public_fetcher.py — bounded fetch cooperation
runtime/sprint_scheduler.py — branch budget coordination pokud nutné
tests/probe_f200c/test_async_batch_public_pipeline.py — nové probe testy
REAL_ARCHITECTURE.md — update public pipeline execution section

Constraints:

- asyncio.gather vždy return_exceptions=True.
- Po gather vždy _check_gathered().
- Žádný model + JS renderer overlap.

Definition of Done:

pytest tests/probe_f200c/ -q → všechny pass
python smoke_runner.py --smoke → OK
public pipeline běží v bounded async batch režimu bez porušení ghost invariantů

Commit message formát: "feat: sprint F200C — add bounded async batching to public pipeline"
```

---

## [DONE] F200D — Pre-Integration Hardening

### 1. Název a cíl fáze

Zachytit již provedené hardening změny, které vznikly po původním plánu: async pattern fixes, scheduler bounds a micro-optimizations.

### 2. Konkrétní soubory vytvořené/upravené

- `utils/execution_optimizer.py` — bezpečnější handling nested async runtimes.
- `network/jarm_fingerprinter.py` — async sleep místo blocking sleep.
- `runtime/sprint_scheduler.py` — bounded latency EMA state.
- `brain/hypothesis_engine.py` — string concat optimalizace v reasoning path.
- `tests/probe_f196c/test_asyncio_run_patterns.py`
- `tests/probe_f196c/test_sprint_scheduler_bounds.py`
- `tests/probe_f196c/test_misc_optimizations.py`

### 3. Definition of Done

- `pytest tests/probe_f196c/ -q` passes.
- Změny jsou dokumentované jako hotové v audit update.
- Nezavádí se nový runtime owner ani nový write path.

### 4. Probe testy

- `tests/probe_f196c/test_asyncio_run_patterns.py`
- `tests/probe_f196c/test_sprint_scheduler_bounds.py`
- `tests/probe_f196c/test_misc_optimizations.py`

### 5. Claude Code prompt pro tuto fázi

Není potřeba generovat další implementační prompt; fáze je již hotová v commitu `2e0f252`.

---

## [DONE] F201A — Smoke Runner Concurrency Contract Repair

**Strategic audit 2026-04-24:** hotovo v commitu `7179a2c feat: sprint F201A — repair smoke concurrency contract`. Ověřeno: `pytest tests/probe_f201a tests/probe_f201b tests/probe_f201c -q` → **33 passed** a `python smoke_runner.py --smoke` → **SMOKE TEST PASSED**.

### 1. Název a cíl fáze

Opravit drift mezi `smoke_runner.py` a aktuální concurrency realitou tak, aby `python smoke_runner.py --smoke` znovu prošel bez obcházení M1 memory invariantů.

### 2. Konkrétní soubory k vytvoření/upravení

- `smoke_runner.py` — aktualizovat smoke assertions na skutečný lazy proxy contract nebo na explicitní adaptive wrapper contract.
- `utils/concurrency.py` — pokud se smoke rozhodne požadovat jednotný wrapper, doplnit bounded `current_limit`/limit introspection bez rozbití existing imports.
- `resource_allocator.py` — případně sladit `AdaptiveSemaphore` constructor kompatibilitu (`initial_value` alias vs `initial_limit`) a root re-export.
- `__init__.py` — jen pokud se mění root export contract.
- `tests/probe_f201a/test_smoke_concurrency_contract.py` — nový probe test pro smoke contract.
- `REAL_ARCHITECTURE.md` — aktualizovat Known test failures a concurrency smoke status.

### 3. Definition of Done

- `pytest tests/probe_f201a/ -q` passes.
- `python smoke_runner.py --smoke` passes.
- `FETCH_SEMAPHORE` při loaded LLM path stále respektuje limit 3 přes `adjust_fetch_workers(3)`.
- Žádný model load/unload path není přesunut mimo `brain/model_lifecycle.py` / existing model manager lifecycle.
- `REAL_ARCHITECTURE.md` už neuvádí AdaptiveSemaphore smoke failure jako aktuální baseline.

### 4. Probe testy

- `tests/probe_f201a/test_smoke_concurrency_contract.py`

### 5. Claude Code prompt pro tuto fázi

```text
[FÁZE F201A — SMOKE RUNNER CONCURRENCY CONTRACT REPAIR]

Kontext:

Audit 2026-04-24 ověřil, že probes F196A-F200C passují: 307 passed.
Současně `python smoke_runner.py --smoke` failuje na driftu mezi smoke expectations a aktuálním concurrency contractem:
- AdaptiveSemaphore.__init__() nepřijímá `initial_value`
- FETCH_SEMAPHORE je lazy `_FetchSemaphoreProxy`, ne AdaptiveSemaphore
- smoke očekává `current_limit` na plain asyncio.Semaphore

Co již existuje a funguje (relevantní pro tuto fázi):

`utils/concurrency.py` poskytuje lazy `FETCH_SEMAPHORE` a `adjust_fetch_workers()`.
`brain/model_manager.py` volá `adjust_fetch_workers(3)` při loaded LLM path a `adjust_fetch_workers(25)` při release.
`resource_allocator.py` stále exportuje `AdaptiveSemaphore` kvůli root compatibility.

Na co navazuješ:

Na F200C/F200D hotový probe baseline. Tahle fáze je blocker pro roadmap DoD, protože každá fáze vyžaduje `python smoke_runner.py --smoke`.

Zadání:

1. Přečti `smoke_runner.py`, `utils/concurrency.py`, `resource_allocator.py`, `__init__.py` a relevantní model lifecycle callers.
2. Rozhodni a implementuj jeden jasný compatibility contract:
   - buď smoke test aktualizuj na lazy proxy reality,
   - nebo zaveď bezpečný wrapper/introspection API tak, aby root export znovu splnil smoke bez rozbití pipeline.
3. Zachovej M1 invariant: při loaded LLM path musí být fetch limit 3.
4. Přidej probe test, který spustí smoke contract bez networku a ověří `adjust_fetch_workers(3)` / restore behavior.
5. Aktualizuj `REAL_ARCHITECTURE.md` Known test failures.

Soubory k úpravě/vytvoření:

smoke_runner.py — aktualizace smoke assertions na skutečný contract
utils/concurrency.py — případný limit introspection/helper
resource_allocator.py — případný constructor alias nebo root compatibility fix
__init__.py — jen pokud se mění export contract
tests/probe_f201a/test_smoke_concurrency_contract.py — nové probe testy
REAL_ARCHITECTURE.md — odstranit resolved smoke failure z current baseline

Constraints:

- FETCH_SEMAPHORE limit=3 při loaded LLM path.
- Fail-soft everywhere.
- Žádné absolutní cesty mimo paths.py.
- Nerozbíjet imports `from hledac.universal import FETCH_SEMAPHORE, AdaptiveSemaphore, adjust_fetch_workers`.
- Nezavádět druhý model lifecycle owner.

Definition of Done:

pytest tests/probe_f201a/ -q → všechny pass
python smoke_runner.py --smoke → OK
pytest tests/probe_f196a tests/probe_f196b tests/probe_f197a tests/probe_f197b tests/probe_f197c tests/probe_f198a tests/probe_f198b tests/probe_f198c tests/probe_f199a tests/probe_f199b tests/probe_f200a tests/probe_f200b tests/probe_f200c -q → stále pass
REAL_ARCHITECTURE.md updated

Commit message formát: "feat: sprint F201A — repair smoke concurrency contract"
```

---

## [DONE] F201B — Truth Docs Drift Repair

**Strategic audit 2026-04-24:** hotovo v commitu `e9fc143 chore: sprint F201B — artifact hygiene, doc updates, srclight cleanup`. Truth docs nyní uvádějí `network.session_runtime._check_gathered`, canonical write seam `async_ingest_findings_batch()` a F201B probe lane prochází.

### 1. Název a cíl fáze

Srovnat dokumentaci s aktuálním stavem po F200C/F200D, protože truth docs samy obsahují staré kontrakty a rozpory.

### 2. Konkrétní soubory k vytvoření/upravení

- `GHOST_INVARIANTS.md` — aktualizovat last-updated a explicitně sjednotit `_check_gathered` import authority podle skutečného kódu (`network.session_runtime` vs `utils.async_helpers`).
- `STORAGE_LAYER_DOCUMENTATION.md` — opravit API guide, který stále ukazuje staré `async_record_shadow_finding(finding)` příklady místo canonical `async_ingest_findings_batch()`.
- `REAL_ARCHITECTURE.md` — odstranit stale řádky typu “forensics/multimodal nejsou canonical import” tam, kde pozdější sekce říkají F198B/F198C active.
- `tests/probe_f201b/test_truth_docs_current.py` — nový probe test pro dokumentační invarianty.

### 3. Definition of Done

- Truth docs neobsahují přímé doporučení používat starý finding write path.
- `REAL_ARCHITECTURE.md` má konzistentní verdict pro `forensics/`, `multimodal/`, `prefetch/`, `monitoring/` a `deep_research/`.
- `GHOST_INVARIANTS.md` odpovídá skutečnému helper importu použitému po `asyncio.gather()`.
- `pytest tests/probe_f201b/ -q` passes.
- `python smoke_runner.py --smoke` passes, pokud F201A už byla sloučena.

### 4. Probe testy

- `tests/probe_f201b/test_truth_docs_current.py`

### 5. Claude Code prompt pro tuto fázi

```text
[FÁZE F201B — TRUTH DOCS DRIFT REPAIR]

Kontext:

REAL_ARCHITECTURE.md je truth doc, ale po rychlém F196A-F200D sledu obsahuje kombinaci aktuálních sekcí a starších dormant verdictů.
GHOST_INVARIANTS.md říká, že `_check_gathered` je z `utils.async_helpers`, zatímco F200C dokumentace a aktuální kód používají `network.session_runtime`.
STORAGE_LAYER_DOCUMENTATION.md obsahuje staré příklady, které mohou svádět k obcházení `async_ingest_findings_batch()`.

Co již existuje a funguje (relevantní pro tuto fázi):

Canonical write path je `knowledge/duckdb_store.py::async_ingest_findings_batch()`.
Canonical sprint owner je `core/__main__.py::run_sprint()`.
F196A-F200C probe lanes passují.

Na co navazuješ:

Na F201A smoke repair. Tahle fáze je dokumentační stabilizace před dalšími feature sprinty.

Zadání:

1. Přečti `REAL_ARCHITECTURE.md`, `GHOST_INVARIANTS.md`, `STORAGE_LAYER_DOCUMENTATION.md` a reálný kód, kterého se tvrzení týkají.
2. Oprav pouze nepravdivé nebo zavádějící části, nemaž celé sekce.
3. V `STORAGE_LAYER_DOCUMENTATION.md` nahraď staré finding write příklady canonical batch ingest ukázkou.
4. V `REAL_ARCHITECTURE.md` sjednoť active/dormant verdict pro moduly, které byly v F197-F200 zapojené.
5. Přidej probe test, který hlídá, že docs už neobsahují banned write-path guidance.

Soubory k úpravě/vytvoření:

GHOST_INVARIANTS.md — update async helper authority a last-updated
STORAGE_LAYER_DOCUMENTATION.md — canonical write examples
REAL_ARCHITECTURE.md — remove/resolve contradictory active/dormant verdicts
tests/probe_f201b/test_truth_docs_current.py — nové probe testy

Constraints:

- Neodstraňuj celé sekce z GHOST_INVARIANTS.md ani STORAGE_LAYER_DOCUMENTATION.md.
- Nepoužívej ARCHITECTURE_MAP.py jako zdroj pravdy.
- Žádné změny runtime kódu, pokud probe neodhalí triviální import-doc mismatch.

Definition of Done:

pytest tests/probe_f201b/ -q → všechny pass
python smoke_runner.py --smoke → OK
REAL_ARCHITECTURE.md, GHOST_INVARIANTS.md a STORAGE_LAYER_DOCUMENTATION.md si neodporují v canonical write/async contracts

Commit message formát: "feat: sprint F201B — repair truth docs drift"
```

---

## [DONE] F201C — Repository Artifact Hygiene And Ghost Bytecode Cleanup

**Strategic audit 2026-04-24:** F201C probe lane je přítomná a zelená. Audit zároveň zjistil, že poslední hygiene commit historicky obsahuje rozsáhlé tracked test/runtime artefakty pod `tests/probe_8bh/runtime/.venv_ddgs/`; Phase 2 proto obsahuje samostatnou kvalitu repozitáře jako průběžný DoD, ale hlavní strategický plán se soustředí na měřitelnou OSINT hodnotu.

### 1. Název a cíl fáze

Vyčistit nezdrojové artefakty, které zamlžují ghost audit a způsobují falešné signály při `find`/`rg` auditech.

### 2. Konkrétní soubory k vytvoření/upravení

- `.gitignore` — doplnit nebo ověřit ignorování `.DS_Store`, `__pycache__/`, `*.pyc`, lokálních audit/cache adresářů a runtime state souborů.
- `tests/probe_f201c/test_repo_artifact_hygiene.py` — nový probe test, který kontroluje, že tracked source neobsahuje ghost `.py` backups ani tracked bytecode.
- Lokální odstranění pouze tracked artefaktů, pokud nějaké existují; untracked osobní cache adresáře nemaž bez explicitního rozhodnutí.
- `REAL_ARCHITECTURE.md` — dokumentovat, že bytecode `.pyc` ghost zbytky nejsou canonical call-sites.

### 3. Definition of Done

- `git ls-files` neukazuje žádné tracked `.pyc`, `__pycache__`, `.DS_Store` nebo `*.bak*` ghost source backups.
- Ghost audit v `tests/probe_f196a/` kontroluje source files, ne bytecode leftovers.
- `pytest tests/probe_f201c/ -q` passes.
- `python smoke_runner.py --smoke` passes, pokud F201A už byla sloučena.

### 4. Probe testy

- `tests/probe_f201c/test_repo_artifact_hygiene.py`

### 5. Claude Code prompt pro tuto fázi

```text
[FÁZE F201C — REPOSITORY ARTIFACT HYGIENE AND GHOST BYTECODE CLEANUP]

Kontext:

Audit 2026-04-24 našel mnoho lokálních `.pyc`, `__pycache__`, `.DS_Store` a cache artefaktů. Některé staré ghost moduly existují už jen jako bytecode v lokálním workspace, což může mást ruční audity.
Untracked osobní/cache adresáře aktuálně existují: `.backup/`, `.codebase-memory/`, `.full-review/`, `rl/.sprint_policy_state.json`.

Co již existuje a funguje (relevantní pro tuto fázi):

F196A source ghost cleanup je hotový a probe lane passuje.
Git source tree už nemá `runtime/intelligence_dispatcher.py`, `runtime/memory_watchdog.py`, `runtime/session_authority.py` ani `rl/marl_coordinator.py`.

Na co navazuješ:

Na F201B truth docs drift repair. Tahle fáze je hygiene guard, ne runtime feature.

Zadání:

1. Audituj tracked files přes `git ls-files`, ne přes lokální cache noise.
2. Ověř `.gitignore` pro `.DS_Store`, `__pycache__/`, `*.pyc`, `*.bak*`, lokální audit/cache dirs a runtime state.
3. Odstraň z git indexu pouze tracked artefakty, pokud existují.
4. Nepřidávej ani nemaž uživatelovy untracked cache/work dirs bez explicitního zadání.
5. Přidej probe test, který failne, pokud se do git tracked tree vrátí bytecode nebo ghost backup source.

Soubory k úpravě/vytvoření:

.gitignore — artifact ignore rules
tests/probe_f201c/test_repo_artifact_hygiene.py — nové probe testy
REAL_ARCHITECTURE.md — artifact hygiene note

Constraints:

- Nemaž untracked uživatelské adresáře bez explicitního souhlasu.
- Žádné runtime behavior změny.
- Ghost audit počítá source `.py` call-sites, ne bytecode.

Definition of Done:

pytest tests/probe_f201c/ -q → všechny pass
python smoke_runner.py --smoke → OK
git ls-files neobsahuje tracked `.pyc`, `__pycache__`, `.DS_Store`, `*.bak*`

Commit message formát: "feat: sprint F201C — add repository artifact hygiene guards"
```

---

## Phase 2 Roadmap

### Strategic Audit Baseline 2026-04-24

Audit četl skutečné soubory v aktuálním repozitáři: `REAL_ARCHITECTURE.md`, `GHOST_INVARIANTS.md`, `STORAGE_LAYER_DOCUMENTATION.md`, `LONGTERM_PLAN.md`, `project_types.py`, `core/__main__.py`, `runtime/sprint_scheduler.py`, `knowledge/duckdb_store.py`, `pipeline/live_public_pipeline.py`, `pipeline/live_feed_pipeline.py`, `intelligence/`, `knowledge/`, `network/`, `forensics/`, `multimodal/`, `graph/`, `rl/`, `discovery/`, `fetching/`, `transport/`, `security/`, `dht/`, `tools/`, `export/` a ukázkové benchmark/export artefakty.

Aktuální runtime baseline:

- `python smoke_runner.py --smoke` → **SMOKE TEST PASSED**.
- `pytest tests/probe_f201a tests/probe_f201b tests/probe_f201c -q` → **33 passed**.
- `async_ingest_findings_batch()` má reálné canonical call-sites z `live_public_pipeline.py`, `live_feed_pipeline.py`, `runtime/sprint_scheduler.py`, `deep_research/probe_runner.py`, `discovery/ti_feed_adapter.py`, `intelligence/ct_log_client.py` a souvisejících producerů.
- Některé schopnosti jsou silné, ale nedostatečně využité v canonical sprint path: `intelligence/identity_stitching.py`, `intelligence/data_leak_hunter.py`, `intelligence/pastebin_monitor.py`, `intelligence/github_secret_scanner.py`, `intelligence/temporal_archaeologist.py`, `knowledge/graph_rag.py`, `knowledge/rag_engine.py`, `network/open_storage_scanner.py`, `network/js_bundle_extractor.py`, `transport/transport_resolver.py`, `security/pii_gate.py`.
- M1 8 GB UMA není jen limit: je to design constraint pro lokální-first OSINT, kde vítězí bounded extraction, streaming pivots, cheap heuristics před ML, lazy model lifecycle a auditovatelná evidence nad masivním crawlingem.

Strategické pravidlo Phase 2: žádná fáze není “jen cleanup”. Každá fáze musí zvýšit analytickou hodnotu, přidat měřitelný signal nebo snížit analyst time-to-answer. Cleanup/hygiene je povinný DoD, ne samostatný cíl.

---

## F202A — Evidence Envelope And Signal Schema

**Proč:** systém už sbírá findings, ale analyst potřebuje vědět “proč je to důležité”, jaký je evidence chain a jaké další pivoty z toho plynou.

### Soubory k vytvoření/upravení

- `knowledge/finding_envelope.py` — nový bounded adapter pro evidence envelope: `FindingEnvelope`, `EvidencePointer`, `SignalFacet`, `to_canonical_finding()`, `from_canonical_result()`.
- `knowledge/duckdb_store.py` — uložit envelope metadata přes existující provenance/payload JSON bez změny canonical write seam.
- `export/sprint_exporter.py` — přidat envelope fields do export handoffu.
- `export/sprint_markdown_reporter.py` — zobrazit evidence pointers, signal facets a analyst next pivots bez fulltext dumpu.
- `tests/probe_f202a/test_evidence_envelope_schema.py` — probe lane.
- `REAL_ARCHITECTURE.md` — zdokumentovat envelope jako active metadata layer, ne nový storage owner.

### Definition of Done

- Každý nový envelope producer zapisuje pouze přes `async_ingest_findings_batch()`.
- Envelope má bounded size: max 8 evidence pointers, max 12 facets, max 5 next pivots na finding.
- Export umí zobrazit evidence pointers i bez LLM.
- `pytest tests/probe_f202a/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### Probe testy

- `tests/probe_f202a/test_evidence_envelope_schema.py`

### Claude Code prompt

```text
[FÁZE F202A — EVIDENCE ENVELOPE AND SIGNAL SCHEMA]

Kontext:

Canonical sprint owner je core/__main__.py::run_sprint().
Canonical write seam je knowledge/duckdb_store.py::async_ingest_findings_batch().
CanonicalFinding je v knowledge/duckdb_store.py a má omezené pole payload_text/provenance; nepřidávej do něj těžký mutable metadata blob bez auditu všech call-sites.

Co již existuje a funguje:

live_public_pipeline.py, live_feed_pipeline.py, deep_research/probe_runner.py a ct_log_client.py už produkují CanonicalFinding.
export/sprint_exporter.py a export/sprint_markdown_reporter.py už tvoří sprint reporty.

Zadání:

Implementuj bounded evidence envelope layer, který umožní každému findingu nést auditovatelný důvod, evidence pointers, signal facets a suggested next pivots. Envelope musí být serializovatelný do payload/provenance bez obcházení async_ingest_findings_batch().

Soubory k úpravě/vytvoření:

knowledge/finding_envelope.py — nový datový adapter a size guards
knowledge/duckdb_store.py — fail-soft ingest/read helpers pro envelope payload
export/sprint_exporter.py — export envelope fields
export/sprint_markdown_reporter.py — markdown rendering evidence pointers
tests/probe_f202a/test_evidence_envelope_schema.py — TDD probe tests
REAL_ARCHITECTURE.md — zdokumentuj active metadata layer

Constraints:

- Žádný nový persistent write path.
- Envelope max size musí být bounded a deterministický.
- Fail-soft: nevalidní envelope nesmí zablokovat finding, jen degradovat na plain finding.
- M1 safe: žádný model load, žádný JS renderer.

Definition of Done:

pytest tests/probe_f202a/ -q → všechny pass
python smoke_runner.py --smoke → OK
export report ukáže evidence pointers a next pivots u envelope findings

Commit message formát: "feat: sprint F202A — add evidence envelope schema"
```

---

## F202B — Entity Extraction And Identity Stitching

**Proč:** pokročilý OSINT nestojí na izolovaných URL, ale na spojování aliasů, e-mailů, domén, handles a organizací do vysvětlitelných identit.

### Soubory k vytvoření/upravení

- `intelligence/entity_signal_extractor.py` — nový lightweight extractor z `CanonicalFinding.payload_text`/`provenance`: email, domain, handle, org-ish names, wallet-ish IDs.
- `intelligence/identity_stitching.py` — přidat bounded canonical adapter pro `IdentityProfile` bez rozšiřování starého example API.
- `runtime/sprint_scheduler.py` — po accepted findings spustit optional identity stitch sidecar a ukládat derived findings přes canonical seam.
- `knowledge/graph_service.py` — zapisovat identity edges jako cross-sprint memory.
- `export/sprint_markdown_reporter.py` — “identity candidates” sekce s confidence a evidence.
- `tests/probe_f202b/test_identity_stitching_canonical.py`.
- `REAL_ARCHITECTURE.md`.

### Definition of Done

- Minimálně e-mail, domain a username handle jsou extrahované deterministicky bez modelu.
- Identity match je explainable: každá edge má signals a evidence pointers.
- Sidecar je bounded: max 500 candidate profiles za sprint, max 2 000 pair comparisons.
- `pytest tests/probe_f202b/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### Probe testy

- `tests/probe_f202b/test_identity_stitching_canonical.py`

### Claude Code prompt

```text
[FÁZE F202B — ENTITY EXTRACTION AND IDENTITY STITCHING]

Kontext:

intelligence/identity_stitching.py už obsahuje IdentityStitchingEngine s M1-friendly fuzzy/string heuristikami, ale není dostatečně wired do canonical sprint path.
knowledge/graph_service.py už existuje jako cross-sprint memory seam.

Co již existuje a funguje:

Canonical findings přicházejí přes duckdb_store.async_ingest_findings_batch().
F202A evidence envelope poskytuje místo pro evidence pointers a signal facets.

Zadání:

Vytvoř deterministic entity extraction sidecar nad accepted findings a zapoj IdentityStitchingEngine tak, aby produkoval derived identity findings a graph edges. Výsledek musí být explainable a bounded pro M1 Air 8 GB.

Soubory k úpravě/vytvoření:

intelligence/entity_signal_extractor.py — nový extractor + tests-first contract
intelligence/identity_stitching.py — canonical adapter a bounds
runtime/sprint_scheduler.py — optional sidecar hook po accepted findings
knowledge/graph_service.py — identity edge upsert helper
export/sprint_markdown_reporter.py — identity candidate rendering
tests/probe_f202b/test_identity_stitching_canonical.py — probe tests
REAL_ARCHITECTURE.md — active identity sidecar docs

Constraints:

- Žádné ML modely v této fázi.
- Max 500 profiles a max 2 000 comparisons per sprint.
- Derived findings jdou přes async_ingest_findings_batch().
- Graph writes jsou fail-soft a advisory.

Definition of Done:

pytest tests/probe_f202b/ -q → všechny pass
python smoke_runner.py --smoke → OK
identity candidates mají confidence, signals a evidence pointers

Commit message formát: "feat: sprint F202B — wire identity stitching sidecar"
```

---

## F202C — Asset Exposure Correlator

**Proč:** CT logs, DNS, BGP, JARM, open storage a exposed service signály jsou silné samostatně, ale analyst potřebuje korelovaný attack surface pohled.

### Soubory k vytvoření/upravení

- `intelligence/exposure_correlator.py` — nový correlator pro domain/IP/service/bucket/cert fingerprints.
- `network/open_storage_scanner.py` — adapter output na exposure signals bez přímé persistence.
- `network/jarm_fingerprinter.py` — bounded fingerprint facade pro correlator.
- `intelligence/ct_log_client.py` — přidat normalized cert/domain facets do envelope metadata.
- `runtime/sprint_scheduler.py` — volitelný exposure correlation hook po CT/public/feed branchích.
- `tests/probe_f202c/test_asset_exposure_correlator.py`.
- `REAL_ARCHITECTURE.md`.

### Definition of Done

- Correlator vytvoří asset exposure finding pro multi-signal asset, např. CT + open storage nebo DNS + JARM.
- External calls mají timeout a graceful fallback.
- Žádné persistence mimo `async_ingest_findings_batch()`.
- `pytest tests/probe_f202c/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### Probe testy

- `tests/probe_f202c/test_asset_exposure_correlator.py`

### Claude Code prompt

```text
[FÁZE F202C — ASSET EXPOSURE CORRELATOR]

Kontext:

Repo už má intelligence/ct_log_client.py, network/bgp_monitor.py, network/jarm_fingerprinter.py, network/open_storage_scanner.py, intelligence/shodan_wrapper.py a passive DNS surfaces.
Tyto signály jsou užitečné, ale zatím nejsou sjednocené do attack surface findingů.

Co již existuje a funguje:

CT log findings jsou canonical.
F202A envelope a F202B entity sidecar dávají metadata/evidence formát.

Zadání:

Vytvoř AssetExposureCorrelator, který bere bounded set signalů z aktuálního sprintu a produkuje explainable exposure findings: exposed host, cert-domain relation, open bucket, suspicious service fingerprint, infra cluster.

Soubory k úpravě/vytvoření:

intelligence/exposure_correlator.py — nový correlator
network/open_storage_scanner.py — normalize scan result DTO
network/jarm_fingerprinter.py — bounded/fail-soft fingerprint facade
intelligence/ct_log_client.py — cert/domain facets pro envelope
runtime/sprint_scheduler.py — exposure hook
tests/probe_f202c/test_asset_exposure_correlator.py — probe tests
REAL_ARCHITECTURE.md — docs

Constraints:

- External calls timeout + graceful fallback.
- asyncio.gather vždy return_exceptions=True + _check_gathered().
- Correlation is bounded: max 1 000 assets, max 3 signals per asset in sprint memory.
- Persist only via async_ingest_findings_batch().

Definition of Done:

pytest tests/probe_f202c/ -q → všechny pass
python smoke_runner.py --smoke → OK
sample correlated exposure finding includes evidence pointers and confidence rationale

Commit message formát: "feat: sprint F202C — correlate asset exposure signals"
```

---

## F202D — Leak And Secret Sentinel

**Proč:** nejvyšší OSINT hodnota často přichází z uniklých tokenů, paste dumpů a GitHub secret stop; systém má moduly, ale potřebuje bezpečný bounded canonical lane.

### Soubory k vytvoření/upravení

- `intelligence/leak_sentinel.py` — nový coordinator nad pastebin/GitHub/data leak moduly.
- `intelligence/data_leak_hunter.py` — bounded single-shot adapter bez background monitoring loopu v canonical sprintu.
- `intelligence/pastebin_monitor.py` — normalize `PasteFinding` na safe redacted finding.
- `intelligence/github_secret_scanner.py` — org/repo search adapter s timeoutem a redaction.
- `security/pii_gate.py` — redaction guard pro secrets/PII před exportem.
- `runtime/sprint_scheduler.py` — optional leak sentinel branch.
- `tests/probe_f202d/test_leak_secret_sentinel.py`.
- `REAL_ARCHITECTURE.md`.

### Definition of Done

- Secret values jsou v exportu masked; raw secret není uložen do reportu.
- Sentinel produkuje canonical findings se `source_type="leak_sentinel"`.
- API absence/rate-limit neblokuje sprint.
- `pytest tests/probe_f202d/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### Probe testy

- `tests/probe_f202d/test_leak_secret_sentinel.py`

### Claude Code prompt

```text
[FÁZE F202D — LEAK AND SECRET SENTINEL]

Kontext:

intelligence/data_leak_hunter.py, intelligence/pastebin_monitor.py a intelligence/github_secret_scanner.py existují, ale canonical sprint path má používat bounded single-shot práci, ne long-running monitoring loop.
security/pii_gate.py existuje jako safety seam.

Co již existuje a funguje:

Canonical write path a F202A envelope.
Scheduler má branch/cycle architecture a fail-soft conventions.

Zadání:

Implementuj LeakSecretSentinel jako bounded optional branch. Převáděj paste/GitHub/breach signály na redacted CanonicalFinding, přidej evidence pointers a nikdy neukládej raw secret do exportu.

Soubory k úpravě/vytvoření:

intelligence/leak_sentinel.py — nový coordinator
intelligence/data_leak_hunter.py — bounded adapter
intelligence/pastebin_monitor.py — safe conversion helper
intelligence/github_secret_scanner.py — timeout/redaction adapter
security/pii_gate.py — redaction guard
runtime/sprint_scheduler.py — optional branch hook
tests/probe_f202d/test_leak_secret_sentinel.py — probe tests
REAL_ARCHITECTURE.md — docs

Constraints:

- No raw secrets in report/export.
- External calls timeout + fail-soft.
- No background monitoring loop in sprint.
- Persist only via async_ingest_findings_batch().

Definition of Done:

pytest tests/probe_f202d/ -q → všechny pass
python smoke_runner.py --smoke → OK
probe verifies raw secret redaction and canonical persistence

Commit message formát: "feat: sprint F202D — add leak and secret sentinel"
```

---

## F202E — Temporal Archaeology And Drift Timelines

**Proč:** OSINT analyst potřebuje časovou osu: kdy se asset objevil, kdy zmizel, kdy se změnil certifikát, metadata nebo identity signal.

### Soubory k vytvoření/upravení

- `intelligence/timeline_synthesizer.py` — nový bounded timeline builder z findings, archive hits, CT timestamps a metadata timestamps.
- `intelligence/temporal_archaeologist.py` — adapter pro canonical timeline events.
- `intelligence/archive_discovery.py` — normalized archive event output.
- `forensics/metadata_extractor.py` — expose timestamp facets pro timeline.
- `export/sprint_markdown_reporter.py` — timeline section.
- `tests/probe_f202e/test_temporal_archaeology_timeline.py`.
- `REAL_ARCHITECTURE.md`.

### Definition of Done

- Timeline obsahuje event type, timestamp, source, confidence a evidence pointer.
- Missing/invalid timestamps jsou fail-soft a nevyhazují celý sprint.
- Timeline export funguje bez LLM.
- `pytest tests/probe_f202e/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### Probe testy

- `tests/probe_f202e/test_temporal_archaeology_timeline.py`

### Claude Code prompt

```text
[FÁZE F202E — TEMPORAL ARCHAEOLOGY AND DRIFT TIMELINES]

Kontext:

Repo má intelligence/temporal_archaeologist.py, intelligence/archive_discovery.py a forensics/metadata_extractor.py. Tyto moduly mohou dát analystovi časový kontext, ale nejsou sjednocené v canonical sprint outputu.

Co již existuje a funguje:

F202A evidence envelope a canonical findings.
Export markdown report already exists.

Zadání:

Vytvoř TimelineSynthesizer, který skládá CT timestamps, archive observations, document metadata timestamps a finding timestamps do bounded explainable timeline. Výsledek přidej do exportu a volitelně jako derived timeline findings přes canonical seam.

Soubory k úpravě/vytvoření:

intelligence/timeline_synthesizer.py — nový timeline builder
intelligence/temporal_archaeologist.py — canonical adapter
intelligence/archive_discovery.py — normalized archive events
forensics/metadata_extractor.py — timestamp facets
export/sprint_markdown_reporter.py — timeline rendering
tests/probe_f202e/test_temporal_archaeology_timeline.py — probe tests
REAL_ARCHITECTURE.md — docs

Constraints:

- No heavy model load.
- Max 200 timeline events per sprint export.
- Invalid timestamps are skipped fail-soft.
- Derived findings persist only through async_ingest_findings_batch().

Definition of Done:

pytest tests/probe_f202e/ -q → všechny pass
python smoke_runner.py --smoke → OK
markdown report includes a bounded timeline with evidence pointers

Commit message formát: "feat: sprint F202E — add temporal archaeology timelines"
```

---

## F202F — Local Graph/RAG Analyst Workbench

**Proč:** po sběru dat musí analyst umět lokálně položit otázku “co spolu souvisí a proč”, bez cloud závislosti a bez načtení velkých modelů vedle rendereru.

### Soubory k vytvoření/upravení

- `knowledge/analyst_workbench.py` — nový query facade nad `graph_service`, `graph_rag`, `rag_engine`, `vector_store`.
- `knowledge/graph_rag.py` — bounded context assembly adapter.
- `knowledge/rag_engine.py` — no-model fallback retrieval path pro M1 safe quick answers.
- `knowledge/vector_store.py` — expose bounded top-k read helper.
- `export/jsonld_exporter.py` — export graph/RAG evidence context.
- `tests/probe_f202f/test_local_graph_rag_workbench.py`.
- `REAL_ARCHITECTURE.md`.

### Definition of Done

- Workbench odpoví na lokální dotaz přes graph/vector evidence bez external network callu.
- Top-k a context bytes jsou bounded.
- Pokud model není loaded, vrátí extractive answer + evidence pointers.
- `pytest tests/probe_f202f/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### Probe testy

- `tests/probe_f202f/test_local_graph_rag_workbench.py`

### Claude Code prompt

```text
[FÁZE F202F — LOCAL GRAPH/RAG ANALYST WORKBENCH]

Kontext:

knowledge/graph_rag.py, knowledge/rag_engine.py, knowledge/vector_store.py a knowledge/graph_service.py existují, ale analyst-facing local query facade chybí.

Co již existuje a funguje:

Cross-sprint graph accumulation a vector/semantic stores.
F202A evidence envelope.

Zadání:

Vytvoř analyst_workbench.py jako lokální read-side facade pro otázky nad findings, graphem a vektory. Musí fungovat i bez LLM: extractive odpověď, related entities, evidence pointers. Pokud LLM použiješ, load/unload pouze přes brain/model_lifecycle.py.

Soubory k úpravě/vytvoření:

knowledge/analyst_workbench.py — nový facade
knowledge/graph_rag.py — bounded context adapter
knowledge/rag_engine.py — no-model fallback retrieval
knowledge/vector_store.py — bounded top-k helper
export/jsonld_exporter.py — evidence context export
tests/probe_f202f/test_local_graph_rag_workbench.py — probe tests
REAL_ARCHITECTURE.md — docs

Constraints:

- No external network calls.
- Model lifecycle only through brain/model_lifecycle.py.
- No model + JS renderer concurrently.
- Context max bytes and top-k are bounded.

Definition of Done:

pytest tests/probe_f202f/ -q → všechny pass
python smoke_runner.py --smoke → OK
workbench returns answer, related entities and evidence pointers for fixture data

Commit message formát: "feat: sprint F202F — add local graph rag workbench"
```

---

## F202G — Hypothesis-Driven Pivot Planner

**Proč:** nejlepší OSINT nástroj nemá jen sbírat; má navrhovat další pivots podle přijatých findings, nejistot a source economics.

### Soubory k vytvoření/upravení

- `runtime/pivot_planner.py` — nový bounded planner pro next sprint queries/pivots.
- `brain/hypothesis_engine.py` — adapter pro cheap hypothesis scoring bez povinného model loadu.
- `tot_integration.py` — optional ToT scoring přes model lifecycle, jen mimo JS renderer path.
- `runtime/sprint_scheduler.py` — přijmout planner suggestions jako advisory source ordering input.
- `discovery/source_registry.py` — mapovat pivot typy na source adapters.
- `tests/probe_f202g/test_hypothesis_pivot_planner.py`.
- `REAL_ARCHITECTURE.md`.

### Definition of Done

- Planner z accepted findings vytvoří max 20 next pivots s reason, expected value a source hint.
- Scheduler bere planner jako advisory only; sprint owner se nemění.
- Model path je optional a M1-safe.
- `pytest tests/probe_f202g/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### Probe testy

- `tests/probe_f202g/test_hypothesis_pivot_planner.py`

### Claude Code prompt

```text
[FÁZE F202G — HYPOTHESIS-DRIVEN PIVOT PLANNER]

Kontext:

brain/hypothesis_engine.py, tot_integration.py a scheduler source economics existují. Chybí jasný bounded planner, který překládá accepted findings na next pivots.

Co již existuje a funguje:

F199A reward source adaptation, F200A prefetch oracle, F202A evidence envelope.

Zadání:

Vytvoř PivotPlanner, který z accepted findings a envelope facets generuje bounded next pivots: domain pivot, identity pivot, leak pivot, archive pivot, graph pivot. Scheduler smí tyto pivots použít jako advisory ordering input, ne jako nový sprint owner.

Soubory k úpravě/vytvoření:

runtime/pivot_planner.py — nový planner
brain/hypothesis_engine.py — cheap scoring adapter
tot_integration.py — optional model-backed scoring přes model_lifecycle
runtime/sprint_scheduler.py — advisory hook
discovery/source_registry.py — pivot type mapping
tests/probe_f202g/test_hypothesis_pivot_planner.py — probe tests
REAL_ARCHITECTURE.md — docs

Constraints:

- Max 20 pivots per sprint.
- Planner failure never blocks export or sprint.
- Model load/unload only via brain/model_lifecycle.py.
- No model + JS renderer concurrently.

Definition of Done:

pytest tests/probe_f202g/ -q → všechny pass
python smoke_runner.py --smoke → OK
planner output includes reason, expected_value, source_hint and evidence pointers

Commit message formát: "feat: sprint F202G — add hypothesis pivot planner"
```

---

## F202H — OPSEC Transport Policy Engine

**Proč:** pokročilý OSINT na lokálním stroji musí být schopný zvolit clearnet/Tor/I2P/nym transport podle risku, bez toho aby OPSEC logika byla rozházená po fetcherech.

### Soubory k vytvoření/upravení

- `runtime/opsec_policy.py` — nový policy engine pro transport posture a renderer/model mutual exclusion.
- `transport/transport_resolver.py` — přijmout policy decision facade.
- `stealth/stealth_session.py` — expose safe capability flags bez side effects.
- `fetching/public_fetcher.py` — aplikovat policy timeout/concurrency hints.
- `network/session_runtime.py` — centralizovat `_check_gathered` usage pro policy fanout tests.
- `tests/probe_f202h/test_opsec_transport_policy.py`.
- `REAL_ARCHITECTURE.md`.

### Definition of Done

- Policy vrací transport, timeout class, renderer_allowed a max_concurrency pro query/source.
- LLM loaded path vynutí `FETCH_SEMAPHORE` limit 3 a renderer disallow.
- Policy failure fallbackuje na safe clearnet bounded mode.
- `pytest tests/probe_f202h/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### Probe testy

- `tests/probe_f202h/test_opsec_transport_policy.py`

### Claude Code prompt

```text
[FÁZE F202H — OPSEC TRANSPORT POLICY ENGINE]

Kontext:

transport/transport_resolver.py, stealth/stealth_session.py, fetching/public_fetcher.py a network/session_runtime.py existují. M1 invariant zakazuje současně LLM model + JS renderer.

Co již existuje a funguje:

FETCH_SEMAPHORE lazy proxy a adjust_fetch_workers().
StealthSession + Tor/Camoufox/nodriver fallback.

Zadání:

Vytvoř runtime/opsec_policy.py jako jediný read-side policy engine pro transport posture. Integruj ho do transport resolveru a public fetcheru jako advisory/safety layer. Policy musí bránit model+renderer konfliktu a dávat timeout/concurrency hints.

Soubory k úpravě/vytvoření:

runtime/opsec_policy.py — nový policy engine
transport/transport_resolver.py — policy integration
stealth/stealth_session.py — safe capability flags
fetching/public_fetcher.py — apply timeout/concurrency hints
network/session_runtime.py — gather helper tests if needed
tests/probe_f202h/test_opsec_transport_policy.py — probe tests
REAL_ARCHITECTURE.md — docs

Constraints:

- No new sprint owner.
- No model lifecycle bypass.
- asyncio.gather return_exceptions=True + _check_gathered().
- Fail-safe fallback is bounded clearnet, not crash.

Definition of Done:

pytest tests/probe_f202h/ -q → všechny pass
python smoke_runner.py --smoke → OK
probe verifies renderer is disabled while model context is active

Commit message formát: "feat: sprint F202H — add opsec transport policy"
```

---

## F202I — Multimodal Evidence Triage

**Proč:** dokumenty a obrázky už umí generovat findings, ale state-of-the-art OSINT potřebuje z nich vytáhnout EXIF/GPS/OCR/loga/text a prioritizovat je jako evidence, ne jako vedlejší přílohy.

### Soubory k vytvoření/upravení

- `multimodal/evidence_triage.py` — nový bounded triage coordinator pro PDF/image artefakty.
- `multimodal/analyzer.py` — emitovat triage facets do envelope.
- `forensics/metadata_extractor.py` — sjednotit EXIF/GPS/timestamp output.
- `tools/ocr_engine.py` — bounded OCR facade s timeoutem.
- `tools/vision_analyzer.py` — optional lightweight image features bez VLM defaultu.
- `pipeline/live_public_pipeline.py` — optional document/image discovery hook bez změny tuple contractu.
- `tests/probe_f202i/test_multimodal_evidence_triage.py`.
- `REAL_ARCHITECTURE.md`.

### Definition of Done

- PDF/image fixture vytvoří canonical document finding s triage facets.
- OCR/EXIF failure neblokuje finding.
- VLM není default; pokud se použije, jde přes model lifecycle a memory guard.
- `pytest tests/probe_f202i/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### Probe testy

- `tests/probe_f202i/test_multimodal_evidence_triage.py`

### Claude Code prompt

```text
[FÁZE F202I — MULTIMODAL EVIDENCE TRIAGE]

Kontext:

multimodal/analyzer.py už produkuje CanonicalFinding(source_type="document"). forensics/metadata_extractor.py, tools/ocr_engine.py a tools/vision_analyzer.py existují jako capability surfaces.

Co již existuje a funguje:

F198C document findings.
F202A evidence envelope.

Zadání:

Vytvoř multimodal/evidence_triage.py, který pro PDF/image artefakty extrahuje bounded facets: title/author, EXIF/GPS, OCR snippets, file hashes, embedded URL/domain hits. Zapoj do live_public_pipeline jako optional hook bez změny live_feed tuple contractu.

Soubory k úpravě/vytvoření:

multimodal/evidence_triage.py — nový coordinator
multimodal/analyzer.py — envelope facets
forensics/metadata_extractor.py — normalized metadata output
tools/ocr_engine.py — bounded OCR facade
tools/vision_analyzer.py — lightweight image features
pipeline/live_public_pipeline.py — optional hook
tests/probe_f202i/test_multimodal_evidence_triage.py — probe tests
REAL_ARCHITECTURE.md — docs

Constraints:

- No VLM by default.
- Model load/unload only via brain/model_lifecycle.py.
- No model + JS renderer concurrently.
- OCR/metadata extraction timeout + fail-soft.

Definition of Done:

pytest tests/probe_f202i/ -q → všechny pass
python smoke_runner.py --smoke → OK
document/image findings include triage facets and evidence pointers

Commit message formát: "feat: sprint F202I — add multimodal evidence triage"
```

---

## F202J — M1 Sustained Sprint Benchmark And Resource Governor

**Proč:** “nejpokročilejší na M1 Air 8 GB” musí být měřitelné: 30min sprint bez swap spirály, bez model/renderer konfliktu a s jasnou throughput/quality metrikou.

### Soubory k vytvoření/upravení

- `runtime/resource_governor.py` — nový governor pro memory pressure, branch concurrency, model lease a renderer lease.
- `brain/model_lifecycle.py` — expose read-only lease/status API bez druhého ownera.
- `runtime/sprint_scheduler.py` — governor advisory input pro branch concurrency a windup.
- `monitoring/sprint_dashboard.py` — zobrazit governor state a memory pressure.
- `benchmarks/m1_sustained_sprint.py` — hermetic benchmark runner.
- `tests/probe_f202j/test_m1_resource_governor.py`.
- `REAL_ARCHITECTURE.md`.

### Definition of Done

- Hermetic benchmark reportuje findings/min, accepted ratio, peak RSS/UMA proxy, model lease count a renderer denied count.
- Governor při critical memory sníží concurrency a nikdy nenutí model+renderer současně.
- `pytest tests/probe_f202j/ -q` passes.
- `python smoke_runner.py --smoke` passes.

### Probe testy

- `tests/probe_f202j/test_m1_resource_governor.py`

### Claude Code prompt

```text

---

## [DONE] F206A–F206J — F206 Seal Phase (2026-04-27)

**Audit 2026-04-27**: F206 series sealed — no new functionality. Architecture documented, verdicts classified, regression matrix established.

### F206 sub-phases

| Phase | Name | Key Change |
|-------|------|-----------|
| F206A | Reproducible Baseline Runner | `run_baseline.py` CLI, `f205-green` profile, known-failure reporting |
| F206B | Shadow Diagnostics Verdicts | ACTIVE/DORMANT verdicts on shadow modules, AST scan for no canonical write path |
| F206C | Lifecycle Runner Extraction | `SprintLifecycleRunner` extracted from `SprintScheduler.run()` |
| F206D | Advisory Runner Extraction | `SprintAdvisoryRunner` extracted from teardown |
| F206E | Windup Scorecard Reporting | `_get_windup_scorecard()` read-only from dormant `windup_engine.py` donor |
| F206F | DHT/IPFS Promotion Gate | Explicit `DHT_PROMOTION_STATUS` and `IPFS_PROMOTION_STATUS` gates |
| F206G | Graph Analytics Activation | `graph_analytics_summary()` bounded, fail-soft, read-only for brief |
| F206H | Target Drift Intelligence | `confidence_drift` gets delta keys + `drift_reasons` |
| F206I | Baseline Regression Extension | `f206-regression` profile includes F204/F205/F206 lanes |
| F206J | Architecture Seal | This document, `REAL_ARCHITECTURE.md` update, seal tests |

### Verdicts after F206

| Module | Verdict | Notes |
|--------|---------|-------|
| `runtime/shadow_inputs.py` | ACTIVE (diagnostic) | Pure read-only shadow inputs |
| `runtime/shadow_parity.py` | ACTIVE (diagnostic) | Pure function diagnostic |
| `runtime/shadow_pre_decision.py` | ACTIVE (diagnostic) | Read-only consumer, no canonical write |
| `runtime/windup_engine.py` | DORMANT (donor) | Defined but never called in production |
| `runtime/sprint_lifecycle_runner.py` | ACTIVE (canonical) | Lifecycle orchestration extracted |
| `runtime/sprint_advisory_runner.py` | ACTIVE (canonical) | Advisory runner extracted |
| `dht/kademlia_node.py` | DORMANT (experimental) | `simulated_no_persist` gate |
| `network/ipfs_client.py` | DORMANT (experimental) | `bounded_gateway_fetch` gate |
| `knowledge/graph_service.py` | ACTIVE (canonical) | Graph analytics for brief, read-only |
| `knowledge/target_memory.py` | ACTIVE (canonical) | Drift intelligence with delta keys |

### Scheduler decomposition (F206C + F206D)

```
SprintScheduler.run()
├── SprintLifecycleRunner     # lifecycle: WARMUP→ACTIVE→WINDUP→TEARDOWN
├── _run_one_cycle()         # canonical branch execution
├── SidecarDispatcher         # sidecar batch dispatch (F205F)
│   └── FindingSidecarBus     # 3-stage staged execution (F205B)
└── SprintAdvisoryRunner     # planner→executor→governor→brief (F206D)
```

### Known failure clusters

| Cluster | Files | Status |
|---------|-------|--------|
| AdaptiveSemaphore smoke | smoke_runner.py | Pre-existing, documented |
| Historical probe lanes | probe_2a, probe_4a, probe_6a–7a | Stale expectations |
| UMA snapshot shape | probe_1b, probe_6b | Shape drift since F195 |

### Probe lanes total (after F206)

- F204: 10 lanes (f204a–j)
- F205: 9 lanes (f205b–j)
- F206: 9 lanes (f206a–i)
- Total in f206-regression: 28 lanes

### Probe testy

- `tests/probe_f206a/test_baseline_runner.py` — 10 tests
- `tests/probe_f206b/test_shadow_verdicts.py` — 15 tests
- `tests/probe_f206c/test_lifecycle_runner_refactor.py` — phase trace, windup guard, abort
- `tests/probe_f206d/test_advisory_runner.py` — AdvisoryRunOutcome, sequential execution, fail-soft
- `tests/probe_f206e/test_windup_scorecard_reporting.py` — 14 tests, windup_scorecard fields
- `tests/probe_f206f/test_dht_ipfs_promotion_gate.py` — 10 tests, promotion gates
- `tests/probe_f206g/test_graph_analytics_activation.py` — 9 tests, graph analytics
- `tests/probe_f206h/test_target_drift_intelligence.py` — 13 tests, drift intelligence
- `tests/probe_f206i/test_baseline_runner.py` — f206-regression probes
- `tests/probe_f206j/test_f206_architecture_seal.py` — architecture seal tests

---

## Exit Criteria For The Whole Roadmap

- Ghost authority surfaces are removed or truly wired. **DONE for source files as of 2026-04-24; F201C probe lane is green.**
- DeepProbe, semantic dedup, embeddings, graph accumulation and enrichers are on the canonical path. **DONE by probe evidence through F200C.**
- Forensics and multimodal produce probe-covered, persisted, auditovatelné outputs. **DONE by F196B/F198B/F198C probes.**
- Scheduler adapts based on real acceptance signal. **DONE by F199A probes.**
- Smoke and F201 stabilization are green. **DONE as of strategic audit 2026-04-24.**
- Phase 2 success means: findings become evidence envelopes, entities become graph memory, exposures/leaks/timelines become analyst-facing signal, and M1 sustained sprint performance is measured rather than assumed.

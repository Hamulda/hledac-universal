# Hledač — Real Architecture (aktualizováno 2026-04-27, F206I)

## F203I — Streaming Embedding Pipeline & LanceDB Pre-warm (2026-04-26)

**Cíl:** Snížit peak RSS embedding fáze a zrychlit cold-start dedup/RAG dotazy na M1 8GB.

**Komponenty:**

| Soubor | Role |
|--------|------|
| `intelligence/streaming_embedder.py` | `StreamingEmbedder` — chunked async batches, MAX_EMBEDDING_BATCH=16, MAX_TEXT_BYTES_PER_FINDING=4096 |
| `embedding_pipeline.py` | `generate_embeddings_streaming()` — non-breaking additive API, yields (ids, embeddings) per batch |
| `knowledge/vector_store.py` | `add_vectors_streaming()` — async chunked insert, yields between chunks |
| `knowledge/ann_index.py` | `prewarm(top_k=128)` — fail-soft ANN index pre-warm pro faster cold-start |
| `runtime/sprint_scheduler.py` | `_run_embedding_sidecar` — používá `StreamingEmbedder` pokud dostupný |

**Bounds:**
- `MAX_EMBEDDING_BATCH=16` — batch size ceiling
- `MAX_TEXT_BYTES_PER_FINDING=4096` — text truncation before embed
- `FETCH_SEMAPHORE=3` while model loaded (via F202H concurrency adapter)

**Guardrails:**
- Model lifecycle via `brain.model_lifecycle.get_model_lifecycle_status()` only
- RAM guard: skip if RSS > 85% high_water via `core.resource_governor.sample_uma_status()`
- Never blocks event loop — all MLX ops in `run_in_executor`

**Benchmark (`benchmarks/m1_embedding_streaming.py --hermetic`):**
- Hermetic mode: synthetic data, no real MLX
- Target: ≥30% peak RSS reduction vs sync path
- Exit code 0 if target met, 1 if not

**Definition of Done:** ✓ pytest tests/probe_f203i/ -q (20/20 pass), smoke_runner OK, benchmark target met

## F204C — Autonomous Pivot Executor (2026-04-26)

**Cíl:** PivotPlanner不再是纯咨询性的——实现bounded executor执行top pivots，通过canonical ingest存储derived findings，并通过HypothesisFeedback记录结果，全程无sync event-loop hack。

**组件:**

| 文件 | 角色 |
|------|------|
| `runtime/pivot_executor.py` | `AutonomousPivotExecutor` — bounded pivot execution, MAX_ACTIVE_PIVOTS=3, MAX_PIVOTS_PER_SPRINT=10, PIVOT_TIMEOUT_S=25.0 |
| `runtime/pivot_planner.py` | `Pivot.pivot_id` — stable unique identifier added to all Pivot objects |
| `runtime/hypothesis_feedback.py` | `async_get_summary()` — called directly via `await` in async context (no nested `new_event_loop`) |
| `runtime/sprint_scheduler.py` | `_run_pivot_executor_advisory()` — teardown调用，在planner之后执行 |

**Bounds:**
- `MAX_ACTIVE_PIVOTS=3` — concurrent pivot executions
- `MAX_PIVOTS_PER_SPRINT=10` — total pivots per sprint
- `PIVOT_TIMEOUT_S=25.0` — per-pivot timeout
- `MAX_PIVOT_FINDINGS=50` — findings cap per pivot

**GHOST_INVARIANTS:**
- `asyncio.gather` with `return_exceptions=True`
- `_check_gathered()` after every gather
- `asyncio.CancelledError` re-raised
- No blocking calls in event loop; network/IO via async clients or `run_in_executor`
- Canonical write path: `async_ingest_findings_batch()`
- Model lifecycle via `brain.model_lifecycle` only — executor must NOT load model
- RAM guard: skip executor if `resource_governor.is_critical` or `is_emergency`
- Bounds on every collection
- Fail-soft: one pivot failure does not block others or sprint

**Teardown调用链:**
```
SprintScheduler.run() → teardown phase
  → _run_pivot_planner_advisory()    # F202G: generate pivots from findings
  → _run_pivot_executor_advisory()    # F204C: execute top pivots via AutonomousPivotExecutor
  → _run_resource_governor_advisory() # F202J: apply governor hints
```

**Definition of Done:** ✓ pytest tests/probe_f204c/ -q (22/22 pass), smoke_runner OK

## F204E/F205J — Analyst Briefing Lifecycle + Target Memory Brief (2026-04-27)

**Cíl:** Zapojit AnalystWorkbench do sprint teardownu — každý sprint produkuje model-free analyst brief: headline, key findings, evidence chains, next actions, open questions. **F205J**: Brief incorporates cross-sprint target memory via `get_target_memory_summary(target_id)`, enabling memory-aware headlines and drift detection.

**F205J změny** (oproti F204E):
- `build_sprint_brief()` accepts `duckdb_store` param — reads target memory fail-soft
- Headline includes sprint count from target memory: `"Sprint N (target X, M prior sprints): ..."`
- Key findings appends `Target memory: N sprints, K cumulative findings, E entities, X exposures, P pivots (drift=R)`
- Open questions include drift signal when `drift_ratio > 1.5` or coverage gaps after 3+ sprints
- Scheduler uses `query` as canonical `target_id` (not `sprint_id`) for cross-sprint memory reads

**Componenty:**

| File | Role |
|------|------|
| `knowledge/analyst_workbench.py` | `AnalystBrief` dataclass (frozen), `build_sprint_brief()` — model-free extractive analysis with target memory |
| `runtime/sprint_scheduler.py` | `_run_analyst_brief_advisory()` v teardown, `get_analyst_brief()`, `self._analyst_brief`; F205J: query→target_id, duckdb_store passed to brief |
| `export/sprint_exporter.py` | `analyst_brief` sekce v JSON exportu (`sanitized_obj["analyst_brief"]`) |
| `export/sprint_markdown_reporter.py` | `_render_analyst_brief_section()` — markdown rendering |
| `core/__main__.py` | `analyst_brief=scheduler.get_analyst_brief()` v ExportHandoff |
| `__main__.py` | `_analyst_brief_for_markdown` module-var + `_print_scorecard_report` wiring |
| `benchmarks/e2e_canonical_benchmark.py` | F205J: `target_memory_summary_present`, `analyst_brief_includes_memory` v aggregate |

**Dataclass — `AnalystBrief` (frozen=True):**
```
sprint_id: str
target_id: str                               # F205J: canonical query, not sprint_id
headline: str                                # F205J: includes prior sprint count from target memory
key_findings: tuple[str, ...]               # F205J: includes "Target memory:" entry
evidence_chain_ids: tuple[str, ...]
next_actions: tuple[str, ...]
open_questions: tuple[str, ...]             # F205J: includes drift signal if drift_ratio > 1.5
confidence: float                            # F205J: +0.1 boost when target memory present
generated_ts: float
```

**Bounds:**
- `MAX_BRIEF_FINDINGS = 20`
- `MAX_BRIEF_CHAINS = 5`
- `MAX_BRIEF_NEXT_ACTIONS = 10`
- `MAX_CONTEXT_BYTES = 8192`

**Teardown volání (F206D):**
```
SprintScheduler.run() → teardown
  → _run_advisory_runner()              # F206D: SprintAdvisoryRunner.run_all_advisories()
       1. pivot_planner  → planned_pivots
       2. pivot_executor → executed_pivots
       3. resource_governor → governor_recorded
       4. analyst_brief → brief_generated
  → _unload_hermes_at_teardown()        # P12: release Hermes engine
```
Each step is fail-soft; `CancelledError` propagates to caller.
Original 4 methods remain as thin delegating wrappers.

**Data flow (F205J):**
1. Teardown calls `workbench.build_sprint_brief(sprint_id, target_id, findings, graph_signal, governor, duckdb_store)`
2. F205J: `get_target_memory_summary(target_id)` called fail-soft — None if duckdb unavailable or target not found
3. RAM guard: governor critical/emergency → minimal brief from counts only (no graph or memory queries)
4. Normal path: extractive analysis + target memory enrichment
   - `_extract_key_findings()` → key findings list
   - If target_memory: append `"Target memory: N sprints, K findings, E entities, X exposures, P pivots (drift=R)"`
   - Headline includes `"N prior sprints"` from target memory
   - Open questions: drift signal if `drift_ratio > 1.5`; coverage gap if 3+ sprints with sparse graph
5. Brief stored in `SprintScheduler._analyst_brief`
6. `core/__main__.run_sprint()` → `ExportHandoff(analyst_brief=scheduler.get_analyst_brief())`
7. `sprint_exporter.export_sprint()` → JSON: `sanitized_obj["analyst_brief"] = _make_serializable(eh.analyst_brief)`
8. `__main__._print_scorecard_report` → markdown: `scorecard_data["analyst_brief"]`

**GHOST_INVARIANTS:**
- `asyncio.gather` with `return_exceptions=True` v teardown phase
- `_check_gathered()` after every gather
- `asyncio.CancelledError` re-raised
- No blocking calls in event loop
- Canonical write path: `async_ingest_findings_batch()` pro synthetic brief finding
- Model lifecycle via `brain.model_lifecycle` only — brief generation must NOT load model
- RAM guard: governor critical/emergency → minimal brief from counts (no graph traversal)
- Bounds on all collections (MAX_BRIEF_*)
- Fail-soft: brief generation errors never crash teardown or export
- F205J: No model load in brief; fail-soft target memory read

**DuckDB schema:** Žádná nová tabulka. Brief je export artifact; může být uložen jako synthetic finding s `source_type="analyst_brief"` přes `async_ingest_findings_batch()`.

**API zachování:**
- `AnalystWorkbench.ask()` zůstává read-side API (model-powered)
- `build_sprint_brief()` je lifecycle-safe advisory (extractive, no model) — F205J adds `duckdb_store` param

**Definition of Done:** pytest tests/probe_f205j/ -q (8/8 pass), python benchmarks/e2e_canonical_benchmark.py --hermetic --runs 3, smoke_runner OK

**Definition of Done:** ✓ pytest tests/probe_f204e/ -q (21/21 pass), smoke_runner OK

## F206D — SprintAdvisoryRunner Extraction (2026-04-27)

**Cíl:** Extrahovat teardown advisory orchestration z `SprintScheduler` do samostatného `SprintAdvisoryRunner`. Scheduler zůstává owner; runner pouze orchestruje 4 advisory kroky v pevném pořadí.

**Refactor-only, žádná nová funkcionalita.**

**Componenty:**

| File | Role |
|------|------|
| `runtime/sprint_advisory_runner.py` | `SprintAdvisoryRunner` — extracted advisory orchestration, `AdvisoryRunOutcome` dataclass |
| `runtime/sprint_scheduler.py` | `_run_advisory_runner()` → teardown entry point; 4 původní metody → tenké delegující wrappery |

**`AdvisoryRunOutcome` (frozen=True):**
```
planned_pivots: int      # 0 if planner skipped/failed
executed_pivots: int    # 0 if executor skipped/failed
governor_recorded: bool  # True if governor evaluate+apply succeeded
brief_generated: bool    # True if analyst brief generated
error: str | None       # None (fail-soft, no top-level error)
```

**Runner execution order (explicit, tested):**
```
1. pivot_planner   → planned_pivots    (F202G: PivotPlanner.plan_pivots)
2. pivot_executor  → executed_pivots   (F204C: AutonomousPivotExecutor.execute_top)
3. resource_governor → governor_recorded (F202J: governor.evaluate + apply_decision)
4. analyst_brief   → brief_generated   (F204E/F205J: workbench.build_sprint_brief)
```

**Teardown call chain:**
```
SprintScheduler.run() → teardown
  → _run_advisory_runner()
       → SprintAdvisoryRunner.run_all_advisories()
            → _run_pivot_planner_advisory()
            → _run_pivot_executor_advisory()
            → _run_resource_governor_advisory()
            → _run_analyst_brief_advisory()
  → _unload_hermes_at_teardown()
```

**Scheduler state mutations (via runner → scheduler access):**
- `scheduler._planned_pivots` — set by planner step
- `scheduler._pivot_execution_results` — set by executor step
- `scheduler._analyst_brief` — set by brief step
- `scheduler._result.sidecars_skipped` — set by governor step
- `scheduler._result.peak_rss_gib` — set by governor step
- `scheduler._result.budget_violations` — incremented by governor step

**GHOST_INVARIANTS:**
- `asyncio.CancelledError` re-raised — never swallowed
- Fail-soft: advisory error never stops runner; partial outcome returned
- No blocking calls in async context
- Canonical write path only via existing seams (duckdb_store, governor)
- Model lifecycle via `brain.model_lifecycle` only
- RAM guard: skip heavy ops when RSS > high_water
- No new persistent write paths introduced

**Backward compatibility:**
- Original 4 methods (`_run_pivot_planner_advisory`, etc.) remain as thin delegating wrappers
- Tests calling these methods directly continue to work
- `SprintAdvisoryRunner` is a new component, not modifying existing seams

**Definition of Done:** pytest tests/probe_f206d/ -q, pytest tests/probe_f204c/ -q, pytest tests/probe_f204e/ -q, pytest tests/probe_f205j/ -q, smoke_runner OK

## F206E — Windup Scorecard Reporting (2026-04-27)

**Cíl:** Reconciliovat dormant `windup_engine.py` donor s aktivní sprint reporting pipeline bez aktivace `run_windup()` jako runneru. Těžit read-only scorecard prvky z dormant windup bez spouštění druhé windup cesty.

**DORMANT path: `run_windup()` — NIKDY nevolaná v produkci**

**Active path: `_get_windup_scorecard()` helper přidává bounded diagnostic fields do `_build_diagnostic_report()`**

**Componenty:**

| File | Role |
|------|------|
| `runtime/windup_engine.py` | **DORMANT (donor)** — `run_windup()` definovaná ale nikdy nevolaná; obsahuje scorecard strukturu jako donor |
| `runtime/sprint_scheduler.py` | `_get_windup_scorecard()` — read-only extrakce bounded windup fields do aktivního diagnostic reportu |

**`_get_windup_scorecard()` read-only sources:**
```
circuit breaker states (transport.circuit_breaker.get_all_breaker_states)
phase durations (from result timing fields: pre_loop_elapsed_s, entered_active_at_monotonic, first_cycle_started_at_monotonic)
graph stats (from _get_graph_signal → graph_service)
peak RSS (from result.peak_rss_gib)
accepted findings (from result.accepted_findings)
sidecar findings (from result.*_findings_produced fields)
branch timeouts (from result.branch_timeout_count)
budget violations (from result.budget_violations)
```

**windup_scorecard do diagnostic report:**
```python
report["windup_scorecard"] = {
    "cb_open_domains": {"domain": "open"|"half_open", ...},  # only open/half_open
    "cb_tracked_count": int,
    "phase_durations": {"warmup_s": float, "active_s": float},
    "graph_nodes": int,
    "graph_edges": int,
    "graph_pgq_available": bool,
    "peak_rss_mb": float,
    "accepted_findings": int,
    "sidecar_findings": {"identity": int, "exposure": int, ...},
    "branch_timeouts": int,
    "budget_violations": int,
}
```

**Bounds:**
- `MAX_WINDUP_SCORECARD_KEYS = 32` — hard limit na počet klíčů
- Priority keys preserved during pruning: cb_open_domains, phase_durations, graph_*, peak_rss, accepted_findings, sidecar_findings, branch_timeouts, budget_violations

**GHOST_INVARIANTS:**
- No model load, no MLX imports
- No GNN inference
- No asyncio.run() or loop.run_until_complete()
- Fail-soft: returns {} when all data sources unavailable
- Bounded collection size

**Updatess:**

- `runtime/windup_engine.py` — **VERDICT: DORMANT (donor/alternate)** — dokumentace potvrzena
- `runtime/sprint_scheduler.py` — přidán `_get_windup_scorecard()` helper
- `runtime/sprint_scheduler.py` — `_build_diagnostic_report()` přidává `windup_scorecard` do reportu

**Tests:** `tests/probe_f206e/test_windup_scorecard_reporting.py` — 14 probe tests

**Definition of Done:** pytest tests/probe_f206e/ -q, pytest tests/probe_f205h/ -q, pytest tests/probe_f205e/ -q, smoke_runner OK, python benchmarks/e2e_canonical_benchmark.py --hermetic --runs 3

## F206F — DHT/IPFS Promotion Gate (2026-04-27)

**Cíl:** Vytvořit explicitní promotion gate pro DHT/IPFS, aby simulované DHT nebylo zaměňováno za produkční OSINT zdroj. Self-hosted důvěryhodnost stojí na pravdivém označení zdrojů.

**DHT PROMOTION GATE:**
- `DHT_PROMOTION_STATUS = "simulated_no_persist"` — explicitní status v kademlia_node.py
- `is_dht_production_ready()` → `False` — gate funkce pro kontrolu readiness
- DHT crawl vrací data, ale NIKDY nevolá `async_ingest_findings_batch`
- `KademliaNode._store_dht_results()` je no-op (Sprint F192B)

**IPFS PROMOTION GATE:**
- `IPFS_PROMOTION_STATUS = "bounded_gateway_fetch"` — explicitní status v ipfs_client.py
- IPFS fetch má timeout (30s) a size cap (10MB MAX_FILE_SIZE_BYTES)
- IPFS fetch fails soft — vrací None na všechny chyby
- Circuit breaker hook je optional a fail-open (try/except obalení)
- `ipfs_content_to_finding_dict()` používá `source_type="ipfs_fetch"` (ne "ipfs")
- `scan_ipfs()` používá `source_type="deep_probe_ipfs"` (ne "deep_probe")

**Componenty:**

| File | Role |
|------|------|
| `dht/kademlia_node.py` | `DHT_PROMOTION_STATUS`, `is_dht_production_ready()` |
| `network/ipfs_client.py` | `IPFS_PROMOTION_STATUS`, `source_type="ipfs_fetch"` |
| `deep_probe.py` | `source_type="deep_probe_ipfs"` pro IPFS findings |
| `deep_research/probe_runner.py` | DHT findings nejsou persistovány |

**Source type tagging (F206F):**
| Source | source_type |
|--------|-------------|
| IPFS gateway fetch | `ipfs_fetch` |
| Deep probe IPFS search | `deep_probe_ipfs` |
| S3 bucket scan | `deep_probe` |
| Discovery URLs | `deep_probe` |

**GHOST_INVARIANTS:**
- DHT: žádné `async_ingest_findings_batch` volání
- IPFS: timeout wrapper kolem všech HTTP operací
- IPFS: MAX_FILE_SIZE_BYTES hard cap (10MB)
- Fail-soft: IPFS fetch vrací None na všechny chyby
- Circuit breaker: optional a fail-open (nesmí blokovat fetch)

**Tests:** `tests/probe_f206f/test_dht_ipfs_promotion_gate.py` — 15 probe tests

**Definition of Done:** pytest tests/probe_f206f/ -q, pytest tests/probe_f197a/ -q, smoke_runner OK

## F204G — Passive Service Fingerprinting (2026-04-26)

**Cíl:** Lokální Shodan/Censys-lite fingerprinting z accepted findings bez aktivního port scanu — deterministický vzor matching z HTTP headers, TLS/cert textu, CT metadata a HTML hints.

**Komponenty:**

| File | Role |
|------|------|
| `intelligence/passive_fingerprint.py` | `PassiveFingerprintAdapter` + `ServiceFingerprint`/`FingerprintResult` dataclasses |
| `runtime/sidecar_bus.py` | `_passive_fingerprint_runner` registered in `DEFAULT_SIDECAR_RUNNERS` |
| `intelligence/exposure_correlator.py` | `SIGNAL_TYPE_PASSIVE_FINGERPRINT` v `extract_signals()` — passive fingerprint facets jako exposure evidence |

**Fingerprint patterns:**
- HTTP headers: Server, X-Powered-By, Via, CF-Ray (nginx, Apache, Cloudflare, WordPress, etc.)
- TLS/cert: subject CN, issuer, SAN entries (Cloudflare, AWS, Azure, Google Cloud, Let's Encrypt)
- CT metadata: certificate transparency log entries
- HTML hints: title, meta generator, script/src CDN patterns

**Dataclasses:**
```
@ServiceFingerprint (frozen=True)
  finding_id: str
  service_name: str
  product: str
  version: str
  confidence: float
  evidence_ids: tuple[str, ...]
  facets: dict[str, str]

@FingerprintResult (frozen=True)
  fingerprints: tuple[ServiceFingerprint, ...]
  scanned_count: int
  skipped_count: int
  elapsed_ms: float
```

**Bounds:**
- `MAX_FINGERPRINT_FINDINGS = 1000` — max fingerprints per sprint
- `MAX_FINGERPRINTS_PER_FINDING = 5` — max fingerprints per finding
- `MAX_PATTERN_BYTES = 4096` — pattern match truncation
- `FINGERPRINT_TIMEOUT_S = 10.0` — sidecar timeout

**DuckDB schema:** Žádná nová tabulka. Fingerprints se ukládají jako CanonicalFinding přes `async_ingest_findings_batch()` se `source_type="passive_fingerprint"`.

**Sidecar registration:**
- `("passive_fingerprint", _passive_fingerprint_runner)` in `DEFAULT_SIDECAR_RUNNERS`
- Canonical write: `async_ingest_findings_batch(derived_findings)`
- Fail-soft: malformed payload_text skipped

**Exposure correlator integration:**
- `SIGNAL_TYPE_PASSIVE_FINGERPRINT = "passive_fingerprint"` constant
- `extract_signals()` handles `source_type="passive_fingerprint"` findings
- Passive fingerprint facets dostupné jako exposure evidence v `signal_facets`

**GHOST_INVARIANTS:**
- `asyncio.gather` with `return_exceptions=True`
- `_check_gathered()` after every gather
- `asyncio.CancelledError` re-raised
- No blocking calls in event loop; regex-only CPU work
- Canonical write path: `async_ingest_findings_batch()`
- RAM guard: skip pokud RSS > high_water
- Bounds on every collection
- Fail-soft: malformed payload_text přeskočit

**Data flow:**
1. Accepted findings → FindingSidecarBus
2. `_passive_fingerprint_runner` extracts HTTP/TLS/CT/HTML signals from payload_text
3. Pattern matching produces `ServiceFingerprint` objects
4. Converted to `CanonicalFinding` list via `to_canonical_findings()`
5. Stored via `async_ingest_findings_batch()` (source_type="passive_fingerprint")
6. ExposureCorrelator reads passive_fingerprint facets from payload_text for correlation

**Definition of Done:** ✓ pytest tests/probe_f204g/ -q (24/24 pass), smoke_runner OK

## F204H — RIR/ASN/WHOIS Bulk Correlator (2026-04-26)

**Cíl:** Bounded RIR/ASN/WHOIS korelace pro IP/domain findings, aby target memory a attribution měly síťové vlastnictví, ASN, org a netblock facets.

**Komponenty:**

| File | Role |
|------|------|
| `intelligence/rir_correlator.py` | `RIRCorrelatorAdapter` + `RIRCorrelation`/`RIRCorrelationResult` dataclasses |
| `runtime/sidecar_bus.py` | `_rir_correlator_runner` registered in `DEFAULT_SIDECAR_RUNNERS` |
| `runtime/sprint_scheduler.py` | `_run_rir_correlator_sidecar()` method + `_accumulate_findings_to_graph()` RIR facet extraction |
| `knowledge/target_memory.py` | RIR facets merged via `TargetMemoryUpdate.exposure_facets` |

**Dataclasses:**
```
@RIRCorrelation (frozen=True)
  ioc_value: str
  ioc_type: str
  asn: str
  org: str
  netblock: str
  country: str
  confidence: float
  evidence_ids: tuple[str, ...]

@RIRCorrelationResult (frozen=True)
  correlations: tuple[RIRCorrelation, ...]
  queried_count: int
  cache_hits: int
  elapsed_ms: float
```

**Bounds:**
- `MAX_RIR_LOOKUPS = 100` — max unique IP lookups per sprint
- `MAX_RIR_RESULTS = 200` — max correlation results
- `RIR_TIMEOUT_S = 5.0` — per-API call timeout
- `RIR_CONCURRENCY = 3` — max concurrent DNS/WHOIS lookups
- `MAX_RIR_CACHE_ENTRIES = 1000` — in-memory FIFO cache

**DuckDB schema:** Žádná nová tabulka. Correlations se ukládají přes `async_ingest_findings_batch()` se `source_type="rir_correlation"` a zároveň se agregují do `target_memory` přes `duckdb_store.async_upsert_target_memory()`.

**Sidecar registration:**
- `("rir_correlator", _rir_correlator_runner)` in `DEFAULT_SIDECAR_RUNNERS`
- Canonical write: `async_ingest_findings_batch(derived_findings)` + `async_upsert_target_memory()`
- Fail-soft: every external API call has timeout + graceful fallback

**GHOST_INVARIANTS:**
- `asyncio.gather` with `return_exceptions=True`
- `asyncio.CancelledError` re-raised
- No blocking DNS/whois in event loop; `run_in_executor` for socket ops
- `asyncio.TimeoutError` caught per-call, never propagated
- Canonical write path: `async_ingest_findings_batch()`
- RAM guard: skip if RSS > high_water via governor
- Bounds on every collection
- Fail-soft: every external API call timeout + graceful fallback

**Target memory integration:**
- RIR facets stored in `exposure_facets[target_id]["rir_asns"]` as `{asn: {org, netblock, country, ioc_type, ioc_value}}`
- Bound: max 100 ASN entries per target
- Deep merge of RIR facets across sprints

**Data flow:**
1. Accepted findings → FindingSidecarBus
2. `_rir_correlator_runner` extracts IP/domain IOCs from findings
3. DNS resolution for domains via `run_in_executor` (socket.gethostbyname)
4. ip-api.com batch HTTP lookup for ASN/org/country/netblock
5. WHOIS lookup (ipwhois) for unresolved domains
6. `RIRCorrelation` list → `CanonicalFinding` list via `to_canonical_findings()`
7. Stored via `async_ingest_findings_batch()` (source_type="rir_correlation")
8. RIR facets merged into `target_memory.exposure_facets["rir_asns"]`

**Definition of Done:** pytest tests/probe_f204h/ -q → 20 passed, smoke_runner OK

## F204I — Social Identity Surface Miner (2026-04-26)

**Cíl:** Rozšířit identity intelligence o pasivní social/web profile facets: usernames, display names, profile URLs, bio links, PGP/email hints a confidence signals bez invazivního scraping chování.

**Komponenty:**

| File | Role |
|------|------|
| `intelligence/social_identity_miner.py` | `SocialIdentityMiner` + `SocialIdentityFacet`/`SocialIdentityResult` dataclasses |
| `runtime/sidecar_bus.py` | `_social_identity_surface_runner` registered in `DEFAULT_SIDECAR_RUNNERS` |
| `runtime/sprint_scheduler.py` | `_run_social_identity_surface_sidecar()` method |
| `intelligence/attribution_scorer.py` | `social_profile_overlap` + `bio_link_overlap` factors in `AttributionConfidenceScorer` |

**Dataclasses:**
```
@SocialIdentityFacet (frozen=True)
  finding_id: str
  platform: str
  username: str
  display_name: str
  profile_url: str
  linked_domains: tuple[str, ...]
  linked_emails: tuple[str, ...]
  confidence: float

@SocialIdentityResult (frozen=True)
  facets: tuple[SocialIdentityFacet, ...]
  scanned_count: int
  skipped_count: int
  elapsed_ms: float
```

**Platform patterns:** GitHub, Twitter, LinkedIn, Mastodon, Keybase, GitLab, HackerNews, Reddit, YouTube, Facebook — extracted from URLs in findings' `payload_text` and `ioc_value` fields.

**Bounds:**
- `MAX_SOCIAL_PROFILES = 200` — max profiles extracted per sprint
- `MAX_LINKS_PER_PROFILE = 20` — max links scanned per profile
- `MAX_SOCIAL_TEXT_BYTES = 4096` — max text bytes scanned
- `SOCIAL_MIN_CONFIDENCE = 0.35` — minimum confidence threshold

**DuckDB schema:** Žádná nová tabulka. Social facets se ukládají přes `async_ingest_findings_batch()` se `source_type="social_identity_surface"` a mohou být použity `AttributionConfidenceScorerem`.

**Sidecar registration:**
- `("social_identity_surface", _social_identity_surface_runner)` in `DEFAULT_SIDECAR_RUNNERS`
- Canonical write: `async_ingest_findings_batch()` via `SocialIdentityMiner.mine()`
- Fail-soft: malformed HTML/payload silently skipped

**Attribution factors (F204I-4):**
- `social_profile_overlap`: compares platform:username sets between candidates (weight: 0.15)
- `bio_link_overlap`: compares email domains and evidence-extracted domains (weight: 0.10)

**GHOST_INVARIANTS:**
- `asyncio.gather` with `return_exceptions=True`
- `asyncio.CancelledError` re-raised
- No blocking calls in event loop; URL parsing is non-blocking
- `asyncio.TimeoutError` caught per-call, never propagated
- Canonical write path: `async_ingest_findings_batch()`
- RAM guard: skip if RSS > high_water * 0.85
- Bounds on every collection

**Data flow:**
1. Accepted findings → FindingSidecarBus
2. `_social_identity_surface_runner` calls `SocialIdentityMiner.mine()`
3. URLs extracted from `payload_text` (JSON envelope + raw text) and `ioc_value`
4. Platform patterns matched: GitHub, Twitter, LinkedIn, Mastodon, Keybase, etc.
5. Confidence scored: base platform bonus + domain link bonus + email bonus
6. Deduplicated by `platform:username` key
7. Stored via `async_ingest_findings_batch()` (source_type="social_identity_surface")
8. Attribution scorer uses social_profile_overlap and bio_link_overlap factors

**AttributionConfidenceScorer changes:**
- Added `social_profile_overlap` factor (weight: 0.15)
- Added `bio_link_overlap` factor (weight: 0.10)
- Both factors use `_social_profile_overlap_score()` and `_bio_link_overlap_score()` methods

**Definition of Done:** pytest tests/probe_f204i/ -q → 22 passed, smoke_runner OK

## F200C Async Batch Public Pipeline (2026-04-23)

**Zdokumentováno** — bounded async batch processing v live_public_pipeline je plně compliant s GHOST_INVARIANTS:

**Async batch execution v `async_run_live_public_pipeline()`** (řádky 2173-2218):
```python
# Vytvoření tasků pro každý discovery hit
tasks: list[asyncio.Task] = []
for hit in hits:
    task = asyncio.create_task(_fetch_and_process_page(...))
    tasks.append(task)

# gather s return_exceptions=True (GHOST_INVARIANTS: asyncio.gather vždy return_exceptions=True)
raw_results = await asyncio.gather(*tasks, return_exceptions=True)

# _check_gathered volán po každém gather (GHOST_INVARIANTS: _check_gathered po každém gather)
from hledac.universal.network.session_runtime import _check_gathered
ok_results, error_results = _check_gathered(raw_results)
```

**Async hygiene invariants (F200C-1 až F200C-5)**:
- `asyncio.gather` vždy používá `return_exceptions=True` — jeden selhání nezruší siblings
- `_check_gathered()` volána po každém `gather` — filtruje exceptiony a loguje je
- `asyncio.CancelledError` propaguje (re-raised, ne swallowed)
- `BaseException` (ne `Exception`) propaguje — `SystemExit`, `KeyboardInterrupt` neztraceny
- Regular `Exception` jdou do `error_results`, ok results do `ok_results`

**Concurrency invariants (F200C-6 až F200C-7)**:
- Pages processed concurrently via `asyncio.create_task` + `gather`, ne sequentially
- `asyncio.Semaphore` limituje concurrency na `fetch_concurrency` parametr
- Memory guard: při UMA_STATE_CRITICAL/EMERGENCY → `effective_concurrency = 1`

**Model/renderer mutual exclusion (F200C-8)**:
- `is_embedding_context_active()` vrací True když embedding model je loaded
- `_fetch_with_camoufox` vrací early prázdný string když embedding context active
- Zabraňuje M1 RAM conflict: model + JS renderer současně

**Fail-soft invariants (F200C-9)**:
- Pipeline pokračuje i když individual page fetch selže
- Výjimky jdou do `error_results`, pipeline vrací `PipelineRunResult` s partial results
- Storage errors: fail-soft, `accepted_count`/`stored_count` zůstávají 0

**Bounded invariants**:
- Semaphore max = `fetch_concurrency` (default 5)
- `_check_gathered` partitionuje results bez memory leak
- žádný nový public API — pouze existující `async_run_live_public_pipeline()`

**Canonical write path maintained**: DuckDB writes still flow through `async_ingest_findings_batch()`.

**Tests**: 16 probe tests v `tests/probe_f200c/test_async_batch_public_pipeline.py`:
- `TestF200CGatherHygiene`: invariant_1-5 (gather pattern, _check_gathered)
- `TestF200CConcurrency`: invariant_6 (overlapping concurrent execution)
- `TestF200CSemaphoreLimiting`: invariant_7 (max 2 concurrent when semaphore=2)
- `TestF200CMemoryGuard`: invariant_8 (UMA CRITICAL → concurrency=1)
- `TestF200CModelRendererExclusion`: invariant_9 (embedding context blocks Camoufox)
- `TestF200CFailSoft`: invariant_10 (exceptions collected, pipeline continues)
- `TestF200CFullGatherPattern`: AST verification of gather pattern

## F200B LanceDB ANN Fast Path (2026-04-23)

**Přidáno** — optional fast-path ANN index for semantic dedup cross-run persistence:
- `knowledge/ann_index.py`: `_ANNIndex` class — LanceDB-backed ANN with cosine similarity. Singleton via `get_ann_index()`. Fail-soft: `_boot_error` set on init failure, all methods return empty/false when unavailable.
- `knowledge/ann_index.py`: `check_ann_duplicate(embedding, text_hash, finding_key)` — public facade. Returns True if ANN search score >= 0.90 and text_hash matches. Upserts on miss.
- `knowledge/ann_index.py`: `reset_ann_index()` — closes and nullifies singleton (sprint teardown).
- `semantic_deduplicator.py`: step 5 now calls `check_ann_duplicate()` before LMDB write — ANN fast path for cross-run duplicate detection.

**ANN fast-path integration in `check_and_cache()`**:
```python
# After LRU check (step 4) → ANN search (step 5) → LMDB store (step 6)
try:
    from hledac.universal.knowledge.ann_index import check_ann_duplicate
    if check_ann_duplicate(emb, text_hash, key):
        self._duplicate_count += 1
        return True
except Exception:
    pass  # Fail-open: ANN errors don't block findings
```

**Data contracts**:
- Dimension: 256d float32 (matches `embedding_pipeline._EMBEDDING_DIM`)
- Metric: cosine similarity (LanceDB `.metric("cosine")`)
- Threshold: 0.90 (same as semantic dedup default)
- Bounded: MAX_ENTRIES=50,000 with 10% LRU eviction on overflow
- Memory guard: init skipped above 6GB RSS

**Fail-open invariants**:
- `_ANNIndex.init()` failure → `_boot_error` set, `ann_search()` returns []
- `check_ann_duplicate()` returns False when ANN unavailable
- Exception in ANN path → caught and returns False (finding accepted)

**Canonical write path maintained**: `duckdb_store.py` still owns all persist writes. ANN is advisory overlay, not a replacement.

**Smoke failures (pre-existing, not introduced by F200B)**:
- `AdaptiveSemaphore.__init__()` signature mismatch (`initial_value` kwarg)
- `FETCH_SEMAPHORE` is `_FetchSemaphoreProxy` not `AdaptiveSemaphore`
- `'Semaphore' object has no attribute 'current_limit'`

## F200A Bounded Prefetch Oracle (2026-04-23)

**Přidáno** — lightweight bounded oracle advisory for scheduler work-item ordering:
- `prefetch/prefetch_oracle_integration.py`: `PrefetchOracleIntegration` class — advisory only, scheduler retains authority. Score composition: historical yield (hot/warm/lukewarm/marginal/cold) + recency bonus + novelty bonus. All methods fail-soft. Bounded: MAX_SOURCE_HISTORY=200 (LRU eviction), MAX_URL_SEEN=50k, MAX_CANDIDATES=100.
- `runtime/sprint_scheduler.py`: `_prefetch_oracle` field (None by default), `inject_prefetch_oracle(oracle)` method, `_sort_work_items_by_economics` incorporates oracle advisory scores, `_process_result` calls `oracle.record_outcome()`, `_reset_result` calls `oracle.reset()`.

**Oracle advisory invariants**:
- Advisory only: oracle SUGGESTS, scheduler DECIDES — oracle score multiplies economics sort key
- Fail-soft: all oracle calls wrapped in `try/except` — oracle failure → scheduler uses default ordering
- Bounded: MAX_SOURCE_HISTORY=200 sources tracked (LRU eviction), MAX_URL_SEEN=50k (LRU eviction), MAX_CANDIDATES=100 per sort
- Score range clamped to [0.1, 3.0]
- Score cache invalidated when `current_cycle` changes
- No network I/O, no MLX/Metal — pure Python, M1-safe

**Integration seam**:
```python
oracle = PrefetchOracleIntegration()
scheduler.inject_prefetch_oracle(oracle)
# During _sort_work_items_by_economics:
#   oracle_scores = oracle.suggest_scores(work_items, current_cycle)
#   oracle_score shifts effective priority within tier/posture band
# After _process_result:
#   oracle.record_outcome(feed_url, fetched, accepted, cycle, seen_new_urls)
# At sprint teardown:
#   oracle.reset()  # clears all state for next sprint
```

**Invariants maintained**:

**Přidáno** — rich terminal dashboard sidecar for live sprint metrics:
- `monitoring/sprint_dashboard.py`: `SprintDashboard` class — rich-based live terminal dashboard. Reads `SprintSchedulerResult` fields directly (data contract: dashboard is NOT a second source of truth, it mirrors scheduler result). Exposes `start()`, `update(result, phase, elapsed_s)`, `finish(result, elapsed_s)`. All render failures are fail-soft (`try/except` throughout, `Live = None` when rich unavailable).
- `runtime/sprint_scheduler.py`: `progress_callback` parameter in `run()` — called after each cycle with `(result, phase_str, elapsed_s)`. Callback is wrapped in `try/except` — exceptions never affect sprint completion.
- `core/__main__.py`: `run_sprint(ui_mode=False)` — when `ui_mode=True`, creates `SprintDashboard` and wires `_on_cycle` progress callback. Dashboard creation is fail-safe — sprint proceeds if dashboard fails.

**Dashboard non-blocking invariants**:
1. `progress_callback` exception is caught by scheduler (`except Exception: pass`)
2. `dashboard.update()` exception is caught by `_on_cycle` wrapper in `__main__`
3. Dashboard `Live` is `None` when rich is not installed — all methods no-op
4. `ui_mode=False` skips all dashboard code path entirely

**Dashboard non-blocking invariants**:
- Dashboard does NOT own lifecycle authority
- Dashboard does NOT create duplicate state — reads from `SprintSchedulerResult`
- Sprint completes regardless of dashboard render failure

## F199A Reward-Driven Source Weight Adaptation (2026-04-23)

**Přidáno** — granular reward signal from `FindingQualityDecision` → source weights:
- `runtime/sprint_scheduler.py`: `_source_quality_feedback` dict (`feed_url → {fetched, accepted}`) — accumulated per `_process_result()` call, bounded at 200 feed_urls, cleared per sprint via `_reset_result()`
- `runtime/sprint_scheduler.py`: `_adapt_source_weights_from_feedback()` — called at sprint teardown; adapts `_source_weights[source_type]` by accepted/total ratio:
  - ratio ≥ 0.7 → +10% (delta=1.10)
  - 0.4 ≤ ratio < 0.7 → +5% (delta=1.05)
  - 0.15 ≤ ratio < 0.4 → neutral (delta=1.00)
  - ratio < 0.15 → -5% (delta=0.95)
  - B.6 bounds enforced: clamped to [0.3, 2.5]
- `runtime/sprint_scheduler.py`: `run()` teardown calls `_adapt_source_weights_from_feedback()` fail-soft (try/except), independent of `_policy_manager`
- `rl/sprint_policy_manager.py`: `update_with_quality_decisions(decisions, feed_url)` stub — no-op, source weights live in scheduler; exists for future per-source reward injection

**Invariants maintained**:
- B.6 bounds: ±20% per sprint → clamp [0.3, 2.5]
- No new fake RL loop — existing `SprintPolicyManager.update()` handles global RL reward
- Scheduler works even when RL helper fails (fail-soft throughout)
- `_source_quality_feedback` reset per sprint via `_reset_result()`

**RL evolution over marl_coordinator.py**:
- `marl_coordinator.py` was ghost code (zero production call-sites, deleted F196A)
- F199A uses the existing `SprintPolicyManager` (opt-in, enabled=False by default)
- Source weight adaptation is scheduler-internal, bounded, explainable
- No resurrection of dead RL coordinator

## F198A Cross-Sprint Graph Accumulation (2026-04-23)

**Přidáno** — cross-sprint graph accumulation:
- `runtime/sprint_scheduler.py`: `_accumulate_findings_to_graph()` — upserts accepted findings (CT log path) to `graph_service` via `upsert_ioc(finding_id, source_type, confidence, sprint_id)`. Fail-soft throughout.
- `runtime/sprint_scheduler.py`: `_get_graph_signal()` — reads `graph_stats()` at teardown and includes in diagnostic report export. Non-blocking.
- `knowledge/graph_service.py`: already had `upsert_ioc`, `upsert_relation`, `find_entity_history`, `graph_stats`, `reset_session` — F198A wires it into canonical sprint path.

**Role distinction maintained**:
- `graph/quantum_pathfinder.py` → **read-side ML overlay only**; `DuckPGQGraph` is the analytics donor backend; NOT a truth store
- `knowledge/graph_service.py` → **sprint memory seam** for cross-sprint entity accumulation; backed by `DuckPGQGraph`; fail-safe throughout

## F206G Graph Analytics Activation (2026-04-27)

**Přidáno** — bounded graph analytics signal for analyst brief and sprint report:
- `knowledge/graph_service.py`: `graph_analytics_summary(top_k=10)` — read-only helper returning top central entities (by degree) and community count from DuckPGQGraph. No persistent writes. Bounds: MAX_GRAPH_ANALYTICS_NODES=500, MAX_GRAPH_ANALYTICS_TOP_K=10.
- `knowledge/analyst_workbench.py`: `build_sprint_brief()` calls `graph_analytics_summary()` and appends up to 2 findings (MAX_GRAPH_ANALYTICS_BRIEF_FINDINGS=2) to key_findings. Fail-soft throughout.
- `tests/probe_f206g/test_graph_analytics_activation.py`: 9 invariants covering bounds, fail-soft, no-writes, and brief integration.

**Output shape** (`graph_analytics_summary`):
```
{
    "top_central_entities": [{"value": "...", "ioc_type": "...", "degree": N}, ...],
    "community_count": int,
    "analytics_available": bool,
    "skipped_reason": str | None,
}
```

**Role distinction maintained**:
- `DuckPGQGraph` (quantum_pathfinder.py) — analytics donor backend, no new authority created
- `graph_service.py` — read-only seam, fail-soft wrapper around DuckPGQGraph
- `analyst_workbench.py` — consumes graph analytics in brief generation (bounded, advisory only)

## F206I — Source Health Summary + Circuit Breaker Coverage (2026-04-27)

**Přidáno** — bounded source health and circuit breaker coverage wired into diagnostic report:
- `runtime/sprint_scheduler.py`:
  - `_get_source_health_summary()` — reads `_source_economics` (per-sprint, in-memory), returns bounded summary (MAX_SOURCE_HEALTH_ENTRIES=100, hot-first ordering)
  - `_get_circuit_breaker_summary()` — reads `get_all_breaker_snapshots()` from `transport.circuit_breaker`, returns total_tracked/open_count/half_open_count/entries (MAX_BREAKER_DOMAINS=500)
  - Both wired into `_build_diagnostic_report()` as `source_health_summary` and `circuit_breaker_state` keys
  - Module-level imports: `get_all_breaker_snapshots`, `get_all_breaker_states`, `MAX_TRACKED_DOMAINS`
- `discovery/ti_feed_adapter.py`: `fetch_malwarebazaar_recent()`, `_handle_malwarebazaar_search()`, `query_rdap()`, and `search_ahmia()` now use `checked_aiohttp_post/get()` — all external domains protected by circuit breaker
- `discovery/duckduckgo_adapter.py`: `_query_shodan_internetdb()` now uses `checked_aiohttp_get()` — shodan internetdb domain protected by circuit breaker
- `tests/probe_f206i/test_source_health_circuit_coverage.py`: 18 invariants covering source health bounds, circuit breaker summary structure, wiring in report, external caller coverage

**Bounds**:
- `MAX_SOURCE_HEALTH_ENTRIES=100` — per-sprint source economics summary
- `MAX_TRACKED_DOMAINS=500` — circuit breaker domain registry (from circuit_breaker.py, unchanged)
- Fail-soft: `source_health_summary` and `circuit_breaker_state` return `{}` on any error

**Existing external callers already CB-protected**:
- `ti_feed_adapter`: urlhaus, threatfox, feodotracker, circl_pdns, crtsh, shodan_internetdb, pastebin, gist (all via `checked_aiohttp_get/post`)
- `duckduckgo_adapter`: mojeek, commoncrawl_cdx, rdap (via `checked_aiohttp_get`)
- `github_secret_scanner`: github search + raw fetch (via `checked_aiohttp_get`)
- `public_fetcher`: own `get_breaker().check_circuit()` + `record_success/failure` in fetch path

**GHOST_INVARIANTS reminder**:
- Both new methods are sync (no `asyncio.gather` needed)
- Fail-soft: empty dict on error, never raise
- No canonical write path touched (read-only, diagnostic)

## F196A Ghost Verdict (2026-04-23)

**Deleted** — zero production call-sites, no canonical wiring:
- `runtime/intelligence_dispatcher.py` — ghost; `attach_dispatcher()` never called from sprint path
- `runtime/memory_watchdog.py` — ghost; `attach_to_dispatcher()` never called from sprint path
- `runtime/session_authority.py` — ghost; zero call-sites confirmed
- `rl/marl_coordinator.py` — stub experiment; zero production call-sites; deleted

**Kept** — active with real imports:
- `runtime/telemetry.py` — ACTIVE; real imports from `__main__.py`, `core/__main__.py`, `metrics_registry.py`

Canonical sprint path is now clean of ghost authority surfaces.

Tento dokument je založený na reálném stavu kódu v repu, ne na `ARCHITECTURE_MAP.py`.

## Canonical Sprint Pipeline

### Hlavní zjištění
- Canonical sprint owner je `run_sprint()` v `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/core/__main__.py:320`.
- Root CLI v `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/__main__.py:3028` je jen shell/dispatcher a pro sprint deleguje do `core.__main__.run_sprint()`.
- Root CLI dnes **nemá** `--mode aggressive` ani `SprintMode` enum. Canonical CLI přepínač je `--aggressive` v `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/core/__main__.py:1193`.
- `SprintMode` enum v aktuálním `runtime/sprint_scheduler.py` **neexistuje**.
- Jediný canonical write path pro findings je `DuckDBShadowStore.async_ingest_findings_batch()` v `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/knowledge/duckdb_store.py:4102`.
- Sprint F197B: `DuckDBShadowStore._assess_finding_quality()` quality gate hook ordering:
  1. Hot cache dedup (in-memory fingerprint)
  2. Persistent LMDB dedup (cross-run fingerprint)
  3. URL-first short-circuit (URL = identity, no entropy check)
  4. Short-string skip (< 8 chars) + semantic dedup check → store
  5. Entropy threshold check (≥ 0.5 bits/char)
  6. **Sprint F197B: Semantic dedup BEFORE LMDB write** → reject on duplicate OR accept on fail-open
  7. Entropy pass → **store to LMDB + hot cache AFTER semantic dedup pass**
- Semantic dedup is fail-open: embedder/LMDB/memory failure → finding accepted (never rejected).

### Reálný datový tok

```text
python -m hledac.universal --sprint <query> [duration]
        |
        v
root __main__.py main()
        |
        | delegates
        v
core/__main__.py run_sprint(query, duration_s, export_dir, aggressive_mode)
        |
        |-- init DuckDBShadowStore
        |-- build SprintSchedulerConfig(aggressive_mode=bool, branch_timeout_budget_s=8.0 if aggressive)
        |-- build SprintLifecycleManager
        |-- build SprintScheduler
        |-- build CTLogClient
        v
SprintScheduler.run(...)
        |
        |-- prewarm Hermes via ModelManager (mode-aware)
        |-- stable mode: _run_one_cycle_stable()
        |     |-- feed branch first
        |     `-- public branch after feed
        |
        `-- aggressive mode: _run_one_cycle_aggressive()
              |-- feed branch task
              |-- public branch task
              `-- CT branch task
        |
        |-- feed -> pipeline/live_feed_pipeline.py async_run_live_feed_pipeline()
        |-- public -> pipeline/live_public_pipeline.py async_run_live_public_pipeline()
        |-- CT -> _run_ct_log_discovery_in_cycle()
        |
        `-- every branch emits CanonicalFinding -> async_ingest_findings_batch()
                    |
                    v
           knowledge/duckdb_store.py quality gate + persistent ingest
                    |
                    v
core/__main__.py runtime truth + report_dict + ExportHandoff
                    |
                    |-- export/sprint_exporter.py export_partial_sprint() during aggressive mode
                    `-- export/sprint_exporter.py export_sprint() at final export
```

### Canonical pipeline modules

| Modul | Public API volaná zvenčí | Vstup | Výstup | Kde se napojuje |
|---|---|---|---|---|
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/__main__.py` | `main()` | `sys.argv` | process exit / delegation | root shell only |
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/core/__main__.py` | `run_sprint()`, `main()` | query, duration, export_dir, aggressive bool | report/export side effects | canonical sprint owner |
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/runtime/sprint_scheduler.py` | `SprintSchedulerConfig`, `SprintSchedulerResult`, `SprintScheduler.run()`, `compute_sprint_intelligence()` | lifecycle, sources, query, store, CT client | scheduler result + intelligence dict | runtime executor |
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/pipeline/live_feed_pipeline.py` | `async_run_live_feed_pipeline()` | `feed_url`, optional store, bounds | `FeedPipelineRunResult` | feed branch |
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/pipeline/live_public_pipeline.py` | `async_run_live_public_pipeline()` | query, store, fetch budgets, Hermes, memory manager, optional feedback hook | `PipelineRunResult` | **public branch + F197C: per-finding embeddings via embedding_pipeline after DuckDB quality gate (fail-soft)** |
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/knowledge/duckdb_store.py` | `DuckDBShadowStore`, `async_ingest_findings_batch()`, `_assess_finding_quality()` | `list[CanonicalFinding]` | `list[FindingQualityDecision | ActivationResult]` | **canonical write seam: hash dedup → semantic dedup (F197B) → LMDB store** |
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/export/sprint_exporter.py` | `export_partial_sprint()`, `export_sprint()` | store + `ExportHandoff`/compat handoff | JSON artifact paths | final export plane |
| `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/tot_integration.py` | `TotIntegrationLayer`, `solve_with_tot()` | prompt, timeout | solution string / empty string | P12 post-storage hypothesis evaluation |

### Important canonical details
- `async_run_live_public_pipeline()` still emits `_SOURCE_TYPE = "live_public_pipeline"` in `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/pipeline/live_public_pipeline.py:42`, not `live_public`.
- P12 hypothesis/ToT block is reachable and runs **before** return in `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/pipeline/live_public_pipeline.py:2667`.
- P12 now does bounded concurrent ToT burst through `asyncio.as_completed(tasks)` in `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/pipeline/live_public_pipeline.py:2718`.
- Aggressive branch fan-out exists in `_run_one_cycle_aggressive()` at `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/runtime/sprint_scheduler.py:1309`.
- Partial export is already wired in aggressive mode through `_maybe_export_partial()` at `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/runtime/sprint_scheduler.py:1872` and `export_partial_sprint()` at `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/export/sprint_exporter.py:79`.

## Aktivní moduly (wired do pipeline)

| Modul | Účel | Vstup | Výstup | Testy |
|---|---|---|---|---|
| `core/__main__.py` | canonical sprint bootstrap, truth closure, export handoff | CLI/query/duration/aggressive flag | scheduler run + export artifact | silné pokrytí napříč `tests/test_e2e_*`, `tests/probe_*` |
| `runtime/sprint_scheduler.py` | lifecycle executor, aggressive/stable cycle runner, CT/public/feed orchestrace, Hermes lifecycle | lifecycle, source list, store, query | `SprintSchedulerResult`, intelligence dict | ~54 test files odkazuje přímo |
| `pipeline/live_feed_pipeline.py` | RSS/Atom discovery -> pattern hits -> CanonicalFinding | feed URL + optional store | `FeedPipelineRunResult` | ~16 přímých test souborů |
| `pipeline/live_public_pipeline.py` | public discovery, fetch policy, CommonCrawl injection, onion/academic/paste/GitHub secret side lanes, P12 | query + store + Hermes + fetch budgets | `PipelineRunResult` | ~16 přímých test souborů |
| `knowledge/duckdb_store.py` | quality gate + WAL/DuckDB canonical persistence + graph/seeds read seams | `CanonicalFinding` batch | quality/storage results | ~62 přímých test souborů |
| `export/sprint_exporter.py` | partial/final export, product value summary, research depth score | `ExportHandoff` | JSON report + seed artifact | 9 přímých test souborů |
| `intelligence/ct_log_client.py` | CRT.SH CT pivot | domain-like query | CT findings | `tests/test_ct_log_pipeline.py`, benchmark doubles |
| `discovery/rss_atom_adapter.py` | feed fetch/parse | feed URL | feed entries | multiple feed probes |
| `discovery/duckduckgo_adapter.py` | public discovery backend | query | discovery hits | public pipeline probes |
| `fetching/public_fetcher.py` | bounded HTTP/JS/Tor capable fetch | URL + policy | `FetchResult` | indirectly tested via public pipeline |
| `patterns/pattern_matcher.py` | pattern matching SSOT | text | pattern hits | dedicated pattern probes |
| `brain/model_manager.py` | Hermes model lifecycle and memory admission | model name | loaded model / release | hypothesis + memory tests |
| `memory/memory_manager.py` | session memory export/history | session id, events | persisted memory/session export | referenced by public pipeline and tests |
| `network/bgp_monitor.py`, `network/ipfs_client.py`, `intelligence/shodan_wrapper.py`, `discovery/ti_feed_adapter.py` | TI/pDNS/Shodan/BGP/IPFS canonical finding ingress | pivot/query/materialized inputs | CanonicalFinding batches | dedicated probe families exist |
| `tools/commoncrawl_adapter.py` | archival URL discovery augmentation | query + session | injected archive hits | `tests/probe_f193a/test_wayback_adapter.py` plus public tests |
| `runtime/telemetry.py` | sprint event logging, phase transitions, EMA metrics | sprint lifecycle events | event ring buffer, JSON log output | `__main__.py`, `core/__main__.py`, `metrics_registry.py` imports |

## Dormant moduly (existují, ale nejsou wired nebo jen okrajově)

### Audited directories

| Modul/adresář | Co reálně dělá | Napojení na canonical sprint path | Testy | Potenciál / priorita |
|---|---|---|---|---|
| `forensics/` | metadata extraction, steganography, digital ghost for file artifacts | **ano** — `forensics/enrichment_service.py` je wired do `duckdb_store._enrich_ct_findings_forensics()` (F195C); F198B rozšířeno o ForensicsResult na URL payload | `tests/probe_f196b/`, `tests/probe_f198b/` | **active — enrichment layer for CT findings** |
| `knowledge/` | mix canonical store + analytics + entity/context modules | **ano**, hlavně `duckdb_store`, `semantic_store`, **graph_service** (F198A cross-sprint accumulation seam) | ano | aktivní jádro + zbytek částečně dormant |
| `graph/` | `quantum_pathfinder.py` (ML overlay/read analytics), `duck_pgq_graph.py` (DuckDB SQL/PGQ donor backend) | **ano**, `graph_service` upsert v sprint_scheduler (F198A); quantum_pathfinder is read-only analytics | ano | read-side analytics overlay; DuckPGQGraph is donor backend, not truth store |
| `loops/` | RL research loop (`ResearchLoop`) | **ano**, public pipeline jej importuje v P16/P17 bloku | vlastní coverage minimální | střední, ale experimentální |
| `federated/` | federated learning, DP, peer/model store; část souborů archivní | nenašel jsem canonical import | 3 test hits | nízká priorita pro OSINT sprint |
| `memory/` | `MemoryManager`, shared memory | **ano**, scheduler/public pipeline | mnoho test hitů | aktivní sidecar |
| `layers/` | univerzální orchestration layers, content/communication/ghost wrappers | nenašel jsem canonical sprint import | bez přímých testů v canonical flow | spíš legacy/integration stack |
| `multimodal/` | fusion/vision encoder, DocumentExtractor, MultimodalEnricher | **ano** — `multimodal/analyzer.py` je wired do sprint_scheduler (F195C); F198C přidává DocumentExtractor pro PDF/image → CanonicalFinding | `tests/probe_f196b/`, `tests/probe_f198c/` | **active — vision + document enrichment layer** |
| `context_optimization/` | cache/compression/MMR/active learning | **ano**, public pipeline používá MMR | bez cílených canonical testů | střední potenciál |
| `coordinators/` | široká vrstva coordinatorů, archive/claims/security atd. | pouze `security_coordinator` je aktivně volán v exportu | bez přímé canonical suite mapy | velká část dormant/secondary |
| `execution/` | `GhostExecutor` | nenašel jsem canonical import | 1 test hit | dormant |
| `network/` | BGP/IPFS/CT scanner/favicon/JARM/JS extraction | **ano**, některé moduly jsou aktivní přes pipeline/adaptery | ano | částečně aktivní, částečně dormancy |
| `planning/` | HTN planner, cost/search, task cache | nenašel jsem canonical sprint import | bez přímých testů v sprintu | dormant, možný future planner |
| `policy/` | Nym/Tor transport policy | nenašel jsem přímé zapojení do canonical sprintu | pár test hitů | dormant |
| `prefetch/` | prefetch cache/oracle/budget/reranker | **částečně** — `prefetch_oracle_integration.py` je aktivní v sprint_scheduler via `inject_prefetch_oracle()` (F200A); `prefetch_oracle.py` (predikční model) zůstává dormant; `prefetch_cache.py` je podpůrná cache, není wired | bez testů | **částečně aktivní — oracle integration active, cache/predictor dormant** |
| `research/` | branch manager, scheduler, prioritizer | nenašel jsem canonical import | 10 test hitů | dormant/alternative scheduler stack |
| `stealth/` | `StealthManager`, `StealthSession`, host telemetry/token bucket | **ano** via `stealth/stealth_session.py` → `public_fetcher.py` (F195C canonical StealthSession wired); `StealthManager` is secondary owner | 3 test hits | **ACTIVE — canonical stealth surface in fetch path** |
| `security/` | audit, destruction, deep research security, obfuscation, encryption | canonical export používá `security_coordinator` z coordinators, ne tyto moduly přímo | několik testů | většinou dormant pro sprint path |
| `text/` | encoding/hash/unicode analyzers + TextAnalyzerFacade | **ano — F205G bounded facade** via `text/text_analyzer_facade.py`; max 3 analyzers, MAX_TEXT_ANALYZER_BYTES=4096, MAX_TEXT_ANALYZER_HINTS=10, fail-soft | ano | **ACTIVE — bounded hook do pattern matching seams, zádné external calls, additive only** |
| `transport/` | Tor/I2P/Nym/circuit breaker/resolver | `core.__main__` importuje `TorTransport`; resolver/testy existují | ano | částečně aktivní |
| `tools/` | velký toolbox včetně CommonCrawl, content extraction, dark web helpers | CommonCrawl je aktivní, zbytek heterogenní | hodně testů | mix active + dormant |
| `rl/` | QMIX/replay buffer/state extraction/action constants | **ano** via `sprint_policy_manager.py` | starší testy (test_sprint58a) | sprint_policy_manager is real (reward contract); marl_coordinator deleted F196A |
| `pipeline/` | canonical feed/public pipelines | **ano** | ano | aktivní |
| `patterns/` | pattern matcher SSOT | **ano** | ano | aktivní |
| `orchestrator/` | secondary thin facade + phase/memory pressure/request routing | canonical sprint owner jej nepoužívá | několik testů | secondary/legacy orchestrator stack |
| `intelligence/` | capability forest: CT, academic, archive, image, blockchain, workflow helpers | část aktivní (`ct_log_client`, `academic_discovery`, paste/github scanners), část dormant | ano | mixed |
| `infrastructure/` | plugin manager, system monitor | nenašel jsem canonical import | bez testů | dormant |
| `discovery/` | adapters for public/feed/TI ingress | **ano** | ano | aktivní |
| `fetching/` | public fetch runtime | **ano** | nepřímo | aktivní |
| `deep_research/` | path discovery + deep utilities | **ano**, volán z `core/__main__.py` při `--deep-probe` flag | `tests/probe_f197a/` | aktivní post-sprint canonical deep research |
| `dht/` | Kademlia crawl/lookups + local graph | `kademlia_node.py` existuje, ale DHT write path je no-op | ano | částečně dormant, not canonical producer today |
| `brain/` | model/embedding/hypothesis/distillation/router stack | část aktivní (`model_manager`, `hypothesis_engine`, `ane_embedder`) | má test coverage | mixed |
| `graph/` | DuckPGQ/graph manager/pathfinder | read-side seam aktivní přes store; full graph orchestration není canonical owner | hodně testů | střední až vysoký potenciál |
| `hypothesis/` | beta-binomial, Dempster-Shafer, EIG, generator | canonical path používá spíš `brain.hypothesis_engine`, ne tento subtree přímo | test hits existují | dormant support math layer |
| `embedding_cache/` | data/cache directory, bez `.py` souborů | žádný canonical import | jen test references | podpůrný asset, ne modul |

### Audited root files

| Soubor | Co reálně obsahuje | Canonical wiring | Testy | Verdikt |
|---|---|---|---|---|
| `enhanced_research.py` | velký dormant monolith (`UnifiedResearchEngine`, `EnhancedResearchOrchestrator`, `DeepResearchRequest`) | nenašel jsem canonical import | 1 hit | legacy candidate / reference only |
| `deep_probe.py` | deep crawler, dorking, path prediction, IPFS/S3 scanners | **ano** via `deep_research/probe_runner.py` -> `async_ingest_findings_batch()` | `tests/probe_f197a/` | aktivní post-sprint deep research lane |
| `tool_registry.py` | typed tool schemas/cost model/registry | canonical sprint owner jej nepoužívá | 7 hitů | useful support layer, dnes ne-wired |
| `evidence_log.py` | canonical evidence ledger | není v canonical sprint path, spíš parallel/legacy ledger plane | 22 hitů | aktivní mimo sprint pipeline |
| `capabilities.py` | capability truth/router/model lifecycle facade | nepůsobí jako canonical sprint dependency | bez přímé sprint integrace | support/diagnostic layer |
| `embedding_pipeline.py` | lazy embedding load/generate/embed query/doc + memory guard | **F197C: wired to live_public_pipeline per-finding storage block** | `tests/probe_f197c/` | **active — per-finding embedding sidecar** |
| `captcha_solver.py` | Apple Vision/CoreML CAPTCHA solver | ne-wired | 3 hitů | niche capability |
| `behavior_simulator.py` | ghost feature placeholder → **F205D: archived to legacy/archived/** | ne-wired | 0 | **ARCHIVED** — re-exported from layers.stealth_layer; zero canonical call-sites |
| `resource_allocator.py` | budget/resource allocator, adaptive concurrency helpers | není canonical owner dependency | 0 | could inform scheduler later |
| `research_context.py` | canonical context carrier types | typová vrstva, ne active runtime in canonical sprint | 0 | useful shared DTO layer |
| `autonomous_analyzer.py` | autonomous analyzer profiles/orchestrator | canonical sprint jej nevolá | 0 | legacy/alternative analysis lane |
| `metrics_registry.py` | lightweight metrics registry | ne-wired | 0 | dormant observability helper |
| `project_types.py` | consolidated type definitions | **ano**, broad shared DTO/types layer | implicit through many modules | active shared type base |

## Dead code / Legacy

### Verdicts (Sprint F205D)
Viz `legacy/archived/ARCHIVE_MANIFEST.py` — full verdicts + rationale per module.

| Module | Verdict | Zdůvodnění |
|--------|---------|------------|
| `behavior_simulator.py` | **ARCHIVED** | Zero canonical call-sites; ghost placeholder; canonical impl in `layers.stealth_layer` |
| `enhanced_research.py` | DORMANT/LEGACY | tool_registry references, legacy/autonomous_orchestrator comments, project_types refs, privacy_enhanced_research active |
| `orchestrator/` | SECONDARY FACADE | smoke_runner.py + tests import; NOT canonical sprint path |
| `federated/` | SECONDARY FACADE | legacy/autonomous_orchestrator lazy imports, prefetch_oracle.py, test suite |

### Jasné legacy nebo secondary surfaces
- Root `__main__.py` obsahuje velký alternativní/deprecated runtime (`_run_sprint_mode`, `_run_public_passive_once`, warmup scaffolding), ale canonical sprint owner je jen `core.__main__.run_sprint()`.
- `enhanced_research.py` je 3058-line dormant orchestrator-like monolith bez canonical wiring.
- `orchestrator/` subtree je secondary facade/orchestration stack, ne canonical sprint path.
- `federated/federated_coordinator_v2.py` a `federated/model_store_v2.py` se samy označují jako archived.
- `behavior_simulator.py` **F205D: archived to legacy/archived/** — ghost placeholder, zero call-sites.
- `knowledge/atomic_storage.py` a `knowledge/corpus_ingester.py` nesou stub/archival signály.

### Duplikované nebo zastaralé authority surfaces
- Root `__main__.py` vs `core/__main__.py`: dvě entrypoint plochy, ale jen jedna canonical sprint authority.
- `research/`, `planning/`, `orchestrator/`, `enhanced_research.py` představují paralelní architektonické směry mimo dnešní sprint pipeline.
- `live_public_pipeline.py` používá `source_type="live_public_pipeline"`, zatímco některé export/plan představy pracují s `live_public`.
- F196A: `intelligence_dispatcher.py`, `memory_watchdog.py`, `session_authority.py`, `marl_coordinator.py` smazány jako ghost moduly s nulovými canonical call-sites.

## Known test failures

> **F206A establishes green baseline separation.** The historical failure clusters below are pre-existing and tracked as known debt. `run_baseline.py --profile f205-green` runs only the F204/F205 green lanes — these failures are reported (not silenced) for traceability.

### Collect / inventory
- `.venv/bin/pytest tests/ --co -q`:
  - `6244 tests collected`
  - `4 skipped`
  - warnings on unknown marks `slow`, `stress`, `timeout` → **resolved by F206A `pytest.ini`**

### Aktuální baseline běh (historical, pre-F206A)
- `.venv/bin/pytest tests/ -q --maxfail=20 --tb=short`
  - `20 failed, 325 passed, 4 skipped` před stopem na `--maxfail=20`

### Green baseline (F206A) — F204 + F205 lanes only
`run_baseline.py --profile f205-green` scope:
- F204 lanes: probe_f204a through probe_f204j (10 lanes)
- F205 lanes: probe_f205b through probe_f205j (9 lanes)
- smoke_runner.py --smoke
- Known historical failures are listed in `run_baseline.py KNOWN_FAILURE_PATTERNS` and reported in JSON output

### Kategorizace prvních reálných failure clusters (historical debt)

| Kategorie | Příklady | Reálný problém |
|---|---|---|
| stale expectations vůči `autonomous_orchestrator` | `tests/probe_2a/test_sprint_2a.py`, `tests/probe_4a/test_lifecycle_4a.py` | testy očekávají staré symboly nebo modul-level attributes (`ActionResult`, `logger`, `time`) |
| drift v UMA snapshot contractu | `tests/probe_1b/test_uma_budget.py`, `tests/probe_6b/test_uma_budget_thresholds.py` | snapshot shape postrádá `is_warn` a `is_emergency` |
| drift ve fetch coordinator API | `tests/probe_4b/test_fetch_4b.py` | test čeká sync `_resolve_host_ips` a `asyncio.to_thread` v jiné podobě |
| chybějící repo artifact | `tests/probe_6a/test_async_hygiene.py`, `tests/probe_7a/test_sprint_7a.py` | `GHOST_INVARIANTS.md` v rootu chybí |
| drift v `utils.mlx_cache` contractu | `tests/probe_6b/test_mlx_cache_limits.py`, `tests/probe_7b/test_mlx_init.py` | chybí `_MLX_CACHE_LIMIT` a related symbol export |

### Known skips / optional deps
- MLX-gated tests: `tests/test_sprint59.py`, `tests/test_sprint60.py`, `tests/test_embedding_prefix_discipline/test_embedding_task.py`
- optional deps/importorskip: `ahocorasick`, `aiohttp_socks`, `stix2`, `polars`, `gliner`, `psutil`
- live/network gates: `tests/test_e2e_pipeline_smoke.py`, `tests/probe_8rb/test_tor_live_clearnet.py`

## Coverage gaps worth calling out


- `embedding_pipeline.py` has direct tests via `tests/probe_f197c/` (F197C wiring).
- `enhanced_research.py`, `resource_allocator.py`, `metrics_registry.py`, `autonomous_analyzer.py` have little or no direct coverage relative to size/importance.

## F195C Probe Coverage (Sprint F196B)

Sprint F195C forensics and multimodal enrichment layers now have dedicated probe coverage:
- `forensics/enrichment_service.py`: 29 probe tests in `tests/probe_f196b/test_forensics_probe_lane.py`
  - Fail-soft: `enrich()` / `enrich_batch()` never raise
  - Lifecycle: `initialize()` / `close()` idempotent
  - Supported-file gating: extension allowlist enforced
  - Return contract: dict keys `metadata`, `steganography`, `ghosts`, `enrichment_available`
  - **Sprint F198B addition**: `"forensics"` key with typed `ForensicsResult` + WHOIS/SSL/DNS/rDNS on URL payloads
  - Counter contract: `SprintSchedulerResult.forensics_enriched_ct_findings`
- `multimodal/analyzer.py`: 28 probe tests in `tests/probe_f196b/test_multimodal_probe_lane.py`
  - Fail-soft: `enrich()` / `enrich_batch()` never raise
  - RAM guard: `_can_run_heavy_vision()` fails-open
  - Lifecycle: `initialize()` / `close()` idempotent
  - Supported-file gating: extension allowlist enforced
  - Return contract: dict keys `vision_embedding`, `fused_embedding`, `clip_score`, `enrichment_available`
  - Counter contract: `SprintSchedulerResult.multimodal_enriched_findings`

## F198B Forensics Metadata on Canonical Findings

Sprint F198B extends forensics enrichment to inject typed ForensicsResult into finding.metadata["forensics"]:
- `forensics/enrichment_service.py`: 20 probe tests in `tests/probe_f198b/test_forensics_metadata_enrichment.py`
  - `ForensicsResult` typed dataclass with fields: `finding_id`, `file_path`, `whois`, `ssl`, `dns`, `rdns`, `enrichment_available`
  - `enrich()` returns dict with `"forensics"` key containing `ForensicsResult.to_dict()`
  - `enrich()` injects into `finding.metadata["forensics"]` when finding has mutable `metadata` dict
  - Domain extraction from URL payload_text via `_extract_domain_from_url()`
  - External lookups: `_whois_lookup()`, `_ssl_lookup()`, `_dns_lookup()`, `_rdns_lookup()` — all with 5s timeout + graceful fallback (None on failure)
  - Fail-soft: all methods return None on failure, never raise
  - URL-based enrichment: findings with URL payload_text are processed via WHOIS/SSL/DNS/rDNS
- 20 probe tests passing: F198B-1 through F198B-7 cover all invariants

## F198C Multimodal Document Findings

Sprint F198C adds document extraction for PDF/image inputs producing CanonicalFinding(source_type="document"):
- `multimodal/analyzer.py`: `DocumentExtractor` and `DocumentResult` added
  - `DocumentResult` typed dataclass: `finding_id`, `file_path`, `file_type`, `text_content`, `page_count`, `metadata`, `extraction_ok`, `to_dict()`
  - `DocumentExtractor.extract()` returns `CanonicalFinding(source_type="document")` or None
  - `DocumentExtractor.extract_batch()` for concurrent extraction (Semaphore-4 bounded, M1 8GB safe)
  - PDF extraction via PyPDF2 (lazy-loaded, `_PYPDF2_AVAILABLE` guard)
  - Image placeholder via PIL (metadata extraction, no OCR in scope)
  - MAX_FILE_SIZE_BYTES=50MB guard, MAX_PDF_PAGES=500 cap, MAX_TEXT_CHARS=200000 cap
  - RAM guard: `_check_ram_guard()` denies at is_critical/is_emergency
  - Fail-soft: all methods return None/empty on failure, never raise
- `pipeline/live_public_pipeline.py`: F198C block added (lazy import of DocumentExtractor, fail-soft)
- `multimodal/__init__.py`: exports `DocumentExtractor` and `DocumentResult`
- 17 probe tests in `tests/probe_f198c/test_multimodal_document_findings.py`
  - Invariants: source_type="document" (exact), fail-soft, RAM guard, size limits, batch concurrency
  - F198C-1 through F198C-10 cover all invariants
- 99 probe tests across F196A-F198C passing

## F197A DeepProbe Canonical Ingest

DeepProbe findings now flow through the canonical persist path:
- `deep_probe.py`: `DeepProbeScanner._make_bucket_finding()` returns `CanonicalFinding` instead of direct persist
- `deep_probe.py`: `scan_ipfs()` returns `List[CanonicalFinding]` instead of direct persist
- `deep_probe.py`: `scan_s3_buckets()` returns `Tuple[List[dict], List[CanonicalFinding]]`
- `deep_research/probe_runner.py`: `run_deep_probe()` collects findings and calls `store.async_ingest_findings_batch()`
- `deep_research/probe_runner.py`: `_make_discovery_findings()` converts DHT URLs to `CanonicalFinding`
- 19 probe tests in `tests/probe_f197a/test_deep_probe_canonical_ingest.py`
  - Invariants: source_type="deep_probe", bounded timeout/depth, fail-safe everywhere
  - `async_ingest_findings_batch()` is the only write path
  - Counter contract: `result["findings_ingested"]` tracks accepted count

## F201A Smoke Concurrency Contract Repair (2026-04-24)

**Přidáno** — repaired smoke_runner.py --smoke concurrency contract:
- `utils/concurrency.py`: `_FetchSemaphoreProxy.limit()` method added — returns current semaphore limit via `get_fetch_semaphore()._value`. Fail-safe, delegates to underlying lazy singleton.
- `smoke_runner.py`: smoke test updated to match actual contract:
  - `AdaptiveSemaphore()` initialized without `initial_value` kwarg — asserts `current_limit == 3` (M1 hard ceiling)
  - `FETCH_SEMAPHORE` checked via `hasattr(limit)` and `FETCH_SEMAPHORE.limit()` instead of `isinstance(AdaptiveSemaphore)`
  - `adjust_fetch_workers` assertions use `FETCH_SEMAPHORE.limit()` instead of `._value`

**Concurrency contract invariants (F201A-1 až F201A-6)**:
- `AdaptiveSemaphore()` initializes with `current_limit=3` (M1 hard ceiling, `_CONCURRENCY_CEILING=3`)
- `FETCH_SEMAPHORE.limit()` returns current underlying semaphore limit via proxy delegation
- `adjust_fetch_workers(3)` sets FETCH_SEMAPHORE to limit 3 (model loaded path)
- `adjust_fetch_workers(25)` restores FETCH_SEMAPHORE to limit 25 (model released path)
- M1 invariant: LLM loaded → fetch limit 3, LLM released → fetch limit 25
- No new model lifecycle owner introduced — `brain/model_manager.py` remains the single owner

**Updated sections**:
- `utils/concurrency.py`: `limit()` method on `_FetchSemaphoreProxy`
- `smoke_runner.py`: all 6 smoke checks updated to match actual lazy proxy contract
- `REAL_ARCHITECTURE.md`: F200A and F199B smoke failure bullets removed (resolved by F201A)

**Tests**: 13 probe tests in `tests/probe_f201a/test_smoke_concurrency_contract.py`:
- `TestF201AAdaptiveSemaphoreContract`: F201A-1 (current_limit=3 default)
- `TestF201AFetchSemaphoreProxy`: F201A-2 (limit() returns int)
- `TestF201AAdjustFetchWorkers`: F201A-3/4 (3→25→3 roundtrip)
- `TestF201ALLMPathConcurrency`: F201A-5/6 (load/release lifecycle)
- `TestF201AImportContract`: root import surface verification
- 320 probe tests across F196A-F201A passing

## F201C Repository Artifact Hygiene (2026-04-24)

**Přidáno** — hygiene guards preventing bytecode and ghost backup artifacts from entering tracked tree:

**Ghost artifact cleanup (F201C-1)**:
- `runtime/telemetry.py.bak_F180F` — removed from git index (was tracked ghost backup)
- `tests/probe_8bh/runtime/.venv_ddgs/` — removed from git index (test venv artifact)
- `.srclight_bak/index.db*` — removed from git index (srclight backup artifact)

**`.gitignore` artifact patterns**:
- Bytecode: `__pycache__/`, `*.pyc`, `*.pyo`, `*.so`
- Ghost backups: `*.bak`, `*.bak_*`, `*_bak_*`, `.bak_*`
- Srclight: `.srclight/`, `.srclight_bak/`
- Probe venv: `tests/probe_*/runtime/.venv*/`

**Probe tests**: `tests/probe_f201c/test_repo_artifact_hygiene.py` — 6 invariants:
- `test_no_tracked_pycache`: `__pycache__/` not tracked
- `test_no_tracked_pyc`: `*.pyc`/`*.pyo` not tracked
- `test_no_tracked_dsvc`: `.DS_Store` not tracked
- `test_no_tracked_bak_files`: ghost backup source not tracked
- `test_no_tracked_srclight_bak`: `.srclight_bak/` not tracked
- `test_no_tracked_probe_venv`: probe venv not tracked

**Ghost audit methodology**: counts source `.py` call-sites, not bytecode. Untracked user directories (`.backup/`, `.codebase-memory/`, `.full-review/`, `rl/.sprint_policy_state.json`) are excluded from hygiene scope.

## F202E: Temporal Archaeology and Drift Timelines (2026-04-24)

**Přidáno** — timeline synthesizer that composes CT timestamps, archive observations, document metadata timestamps, and finding timestamps into bounded explainable timeline:

**Timeline Synthesizer** (`intelligence/timeline_synthesizer.py`):
- `TimelineSynthesizer` — event aggregation from multiple sources
- `TimelineEvent` — single timestamped event (ts, event_type, source, description, entity_id, confidence, evidence)
- `TimelineMetadata` — aggregate stats (total_events, oldest/newest ts, event_types, sources)
- `SynthesizedTimeline` — complete timeline with events and metadata

**Event Sources**:
- CT timestamps: `add_ct_events()` — extracts ts from `source_type="ct_log"` findings
- Archive observations: `add_archive_events()` — from ArchiveResult/ArchivedVersion objects
- Document timestamps: `add_document_timestamps()` — from DocumentMetadata created/modified fields
- Finding timestamps: `add_finding_events()` — generic finding ts extraction

**Bounds** (F202E-3):
- `MAX_TIMELINE_EVENTS = 200` — hard cap on output events
- `MAX_EVENT_AGE_DAYS = 1825` — 5 years, events older excluded
- Invalid timestamps (NaN, negative, far future) skipped fail-soft (F202E-4)

**Temporal Archaeologist Adapter** (`intelligence/temporal_archaeologist_adapter.py`):
- `TemporalArchaeologistAdapter` — wraps TimelineSynthesizer for sprint pipeline
- `synthesize_timeline()` — multi-source aggregation → `SynthesizedTimeline`
- `to_derived_findings()` — converts timeline to CanonicalFinding with source_type="temporal_archaeology"

**Sprint Scheduler Hook** (`runtime/sprint_scheduler.py`):
- `_run_temporal_archaeology_sidecar()` — async sidecar after CT findings accepted
- Filters CT findings (`source_type="ct_log"`) and synthesizes timeline
- Derived findings ingested via `async_ingest_findings_batch()` — canonical write path
- `timeline_findings_produced` counter in `SprintSchedulerResult`
- Non-blocking, fail-soft (F202E-8)

**Markdown Rendering** (`export/sprint_markdown_reporter.py`):
- `_render_timeline_section()` — renders timeline with entity_id, event count, time span, event type/source breakdown, bounded event list
- Bounded at 5 timelines displayed, 50 events per timeline

**No Model Load** (F202E-12):
- Pure Python timestamp processing — no MLX, no transformers
- O(n log n) sort for timeline ordering (n ≤ 200)

**Tests**: `tests/probe_f202e/test_temporal_archaeology_timeline.py` — 35 tests:
- F202E-1: source_type="temporal_archaeology" for derived findings
- F202E-2: payload_text contains serialized timeline JSON
- F202E-3: MAX_TIMELINE_EVENTS=200 cap applied
- F202E-4: Invalid timestamps skipped fail-soft
- F202E-5: async_ingest_findings_batch integration
- F202E-6: Sidecar wiring in SprintScheduler
- F202E-7: timeline_findings_produced in result
- F202E-8: Fail-soft error handling
- F202E-9: Event types (ct_observed, archive_snapshot, document_dated, finding_accepted)
- F202E-10: Timeline sorted ascending
- F202E-11: Markdown rendering
- F202E-12: No model load

---

## F202I: Multimodal Evidence Triage (2026-04-24)

**Přidáno** — bounded triage extraction from PDF/image artifacts discovered in sprint runs. Extracts: title/author, EXIF/GPS, OCR snippets, file hashes, embedded URL/domain hits.

**EvidenceTriageCoordinator** (`multimodal/evidence_triage.py`):
- `TriageFacets` dataclass: title, author, exif, gps, ocr_snippets, file_hashes, embedded_urls, embedded_domains, triage_complete
- `EvidenceTriageCoordinator.extract_triage_facets(file_path, source_type)` — orchestrator method
- Metadata extraction via `UniversalMetadataExtractor` (forensic metadata: title, author, EXIF, GPS, hashes)
- OCR text extraction via `VisionOCR` (macOS Vision framework — no VLM)
- URL/domain hit detection in OCR text via regex
- All operations are **fail-safe**: partial facets returned on any error

**Bounds** (F202I-3, F202I-4, F202I-17):
- `MAX_URL_HITS = 20` — max embedded URLs/domains per file
- `MAX_OCR_SNIPPETS = 10` — max OCR text snippets stored
- `MAX_OCR_CHARS = 5000` — max OCR characters per file
- `MAX_FILE_SIZE_FOR_TRIAGE = 100MB` — files larger are skipped
- `METADATA_TIMEOUT_S = 30.0` — metadata extraction timeout
- `OCR_TIMEOUT_S = 30.0` — OCR timeout

**No VLM** (F202I-15):
- Uses `VisionOCR` (macOS Vision OCR) only — no VisionEncoder, no MambaFusion, no MLX models
- Model load/unload only via `brain/model_lifecycle.py`

**Evidence Envelope** (`multimodal/analyzer.py` `_build_document_envelope()`):
- Combines F202A envelope pattern (audit_reason, evidence_pointers, signal_facets, suggested_pivots) with F202I triage facets
- F202I triage section: title, author, exif, gps, ocr_snippets, file_hashes, embedded_urls, embedded_domains
- `content_preview`: first 1000 chars of extracted text
- Bounded at `_MAX_ENVELOPE_SIZE = 4098` bytes

**DocumentExtractor Integration** (`multimodal/analyzer.py`):
- `DocumentExtractor.extract()` calls `EvidenceTriageCoordinator.extract_triage_facets()` before building `CanonicalFinding`
- Resulting `CanonicalFinding(source_type="document")` has `payload_text` containing triage envelope JSON
- RAM guard via `_check_ram_guard()` — blocks when UMA critical/emergency

**Sprint Scheduler Hook** (`runtime/sprint_scheduler.py`):
- `_evidence_triage_adapter` field (lazy, None until first use)
- `_evidence_triage_findings_count` counter in `SprintSchedulerResult`
- `_run_evidence_triage_sidecar(findings, store, query)` — counts document findings with triage envelopes
- Called after `_run_temporal_archaeology_sidecar()` — non-blocking sidecar
- **No change to live_feed tuple contract** — sidecar is purely observational

**Tests**: `tests/probe_f202i/test_multimodal_evidence_triage.py` — 36 tests:
- F202I-1: Lazy initialization of metadata extractor
- F202I-2: extract_triage_facets returns TriageFacets
- F202I-3: URL/domain extraction bounded at MAX_URL_HITS=20
- F202I-4: OCR snippets bounded at MAX_OCR_SNIPPETS=10
- F202I-5: File hashes extracted from GenericMetadata
- F202I-6: TriageFacets.to_dict() includes all required fields
- F202I-7: DocumentExtractor.extract() calls triage and builds envelope
- F202I-8: _build_document_envelope produces JSON with triage facets
- F202I-9: Envelope bounded at _MAX_ENVELOPE_SIZE=4098
- F202I-10: _run_evidence_triage_sidecar counts document findings with triage
- F202I-11: SprintSchedulerResult.evidence_triage_findings_count field exists
- F202I-12: _evidence_triage_adapter field exists in SprintScheduler
- F202I-13: Sidecar called after F202E temporal archaeology sidecar
- F202I-14: Fail-soft throughout
- F202I-15: No VLM (VisionOCR only)
- F202I-16: RAM guard blocks when UMA tight
- F202I-17: Size guard skips files > 100MB
- F202I-18: OCR timeout after 30s
- F202I-19: Metadata timeout after 30s
- F202I-20: No live_feed tuple contract change

---

## F202J: M1 Sustained Sprint Governor (2026-04-24)

**Přidáno** — advisory safety layer for M1 8GB branch concurrency, model lease, and renderer lease.

**M1ResourceGovernor** (`runtime/resource_governor.py`):
- Advisory safety layer — NOT a sprint owner. Reads from canonical sources only.
- Governs branch concurrency, model lease, renderer lease via fail-soft decisions
- `GovernorDecision` dataclass: `fetch_limit`, `allow_renderer`, `allow_model_load`, `branch_concurrency`, `reason`, `uma_state`, `model_loaded`
- `GovernorSnapshot` dataclass: current state for dashboard rendering
- `evaluate()` async: evaluates governor decisions per cycle
- `apply_decision()` async: applies decisions to runtime surfaces via `adjust_fetch_workers()`
- Singleton via `get_governor()`

**Read-only surfaces**:
- `brain/model_lifecycle.get_model_lifecycle_status()` — model lease state
- `core/resource_governor.sample_uma_status()` — UMA memory state
- `utils/concurrency.adjust_fetch_workers()` — fetch concurrency control

**Decision logic**:
- CRITICAL/EMERGENCY UMA → `fetch_limit=3`, `allow_renderer=False`, `branch_concurrency=1`
- Model loaded → `fetch_limit=3`, `allow_renderer=False`, `branch_concurrency=2`
- WARN UMA → `fetch_limit=12` (half of 25), `branch_concurrency=3`
- Normal → `fetch_limit=25`, `branch_concurrency=4`

**Sprint Scheduler integration** (`runtime/sprint_scheduler.py`):
- `_governor` field: lazy singleton, initialized in `run()` via `get_governor()`
- `_run_resource_governor_advisory()` at TEARDOWN: evaluates and applies governor decision
- Aggressive branch concurrency uses `decision.branch_concurrency` via semaphore limit
- `_reset_result()` resets governor reference (singleton stays across sprints)
- Governor is advisory only — scheduler retains all authority

**Dashboard** (`monitoring/sprint_dashboard.py`):
- Governor state row: uma state, fetch limit, branches, model status, denied counts
- Fail-safe: errors silently ignored (optional dashboard info)

**Hermetic benchmark** (`benchmarks/m1_sustained_sprint.py`):
- `--hermetic` flag (default True) — canned data, no network, no MLX
- Measures: findings/min, acceptance ratio, RSS peak, UMA state summary, denied counts
- M1 8GB ceiling: `M1_8GB_CEILING_MB = 6.5 * 1024`
- Writes bounded summary to stdout and optional JSON

**Key invariants**:
- Model lifecycle authority remains `brain/model_lifecycle.py` (read-only client)
- No model + JS renderer concurrently — enforced via `allow_renderer=False` when model loaded
- FETCH_SEMAPHORE limit=3 while model loaded — via `adjust_fetch_workers(3)`
- Governor fails soft — safe defaults on any error

**Tests**: `tests/probe_f202j/test_m1_resource_governor.py` — 10 probe tests (F202J-1 through F202J-10)

---

## F205E: Hermetic E2E Canonical Benchmark (2026-04-27)

**Přidáno** — `benchmarks/e2e_canonical_benchmark.py` hermetic E2E benchmark for canonical F204/F205 pipeline metrics.

**Metrics measured**:
- `findings_per_minute` — throughput: `(stored_count / wall_clock_s) * 60`
- `dedup_ratio` — store acceptance rate: `stored_count / accepted_count`
- `sidecar_total_ms` — wall-clock bus execution time (all stages)
- `per_sidecar_ms` — per-runner elapsed_ms from `SidecarRunResult` records
- `peak_rss_mb` — RSS memory ceiling check vs M1 8GB bound
- `accepted_count`, `stored_count` — from `MockDuckDBStore` quality gate

**Hermetic mode** (default):
- Synthetic `CanonicalFinding`-like dicts via `_make_synthetic_finding()`
- `MockDuckDBStore` simulates quality gate with `SYNTHETIC_ACCEPT_RATE=0.70`
- `LightRunner` calls `store.async_ingest_findings_batch()` then sleeps `SIDECAR_LIGHT_LOAD_MS=5ms`
- No network imports, no MLX imports, no model lifecycle
- `asyncio.gather(return_exceptions=True)` + `_check_gathered()` pattern
- `FindingSidecarBus` with all SIDECAR_STAGES runners registered

**CLI**: `python benchmarks/e2e_canonical_benchmark.py --hermetic --runs N --output PATH`

**Output schema** (`aggregate` dict):
- `findings_per_minute`, `dedup_ratio`, `sidecar_total_ms`, `stored_count`, `accepted_count`, `peak_rss_mb`, `memory_ceiling_ok`

**GHOST_INVARIANTS enforced**:
- No blocking calls in event loop (all I/O via asyncio.sleep)
- `gather(return_exceptions=True)` + `_check_gathered()` per stage
- Fail-soft: error runner returns `SidecarRunResult` with `skipped_reason`, not exception
- Bounded: `HERMETIC_MAX_FINDINGS=200`, mock store tracks dedup via `_seen_ids`

**Tests**: `tests/probe_f205e/test_f205e_canonical_benchmark.py` — 18 probe tests (F205E-1 through F205E-15)

---

## F203J: Quantization Selector & Adaptive Inference Budget (2026-04-26)

**Přidáno** — model load quantization and inference budget advisory based on UMA snapshot. Model load is no longer binary; governor selects quantization tier and token/latency budget.

**QuantizationSelector** (`brain/quantization_selector.py`):
- Advisory layer selecting quantization and inference budget based on UMA snapshot
- `InferenceBudget` dataclass: `max_tokens`, `max_latency_ms`, `quantization`, `reason`
- `QuantizationDecision` dataclass: full decision record with `free_uma_gib`, `allowed`
- Policy (always-on, fail-soft):
  - `Q4_K_M` — default for CRITICAL/EMERGENCY, or when free < 1.5 GiB
  - `Q5_K_M` — when free >= 1.5 GiB (WARN or OK)
  - `Q8_0` — only when OK + free >= 2.5 GiB + explicitly safe (no swap, no io_only)
- `free_uma_hint()` helper returns free UMA GiB from snapshot

**Model lifecycle integration** (`brain/model_lifecycle.py`):
- `_selected_quantization` module variable tracks active quantization tier
- `get_selected_quantization()` — read-only status surface
- `set_selected_quantization()` — internal setter (called by QuantizationSelector)

**ModelManager integration** (`brain/model_manager.py`):
- `_load_model_async()` for HERMES: consults QuantizationSelector before load
- Calls `sample_uma_status()` → `QuantizationSelector.select()` → `InferenceBudget`
- If budget `max_tokens=0` (governor denied), raises RuntimeError and skips load
- Sets selected quantization via `set_selected_quantization()`
- Governor denies = `allow_model_load=False` in GovernorDecision

**Governor extension** (`runtime/resource_governor.py`):
- `GovernorDecision.free_uma_gib` — free UMA GiB hint for QuantizationSelector
- `GovernorSnapshot.free_uma_gib` — snapshot field for dashboard
- `evaluate()` computes and returns `free_uma_gib` from `uma.system_available_gib`
- `snapshot()` reads live `free_uma_gib` from `sample_uma_status()`

**Sprint Scheduler integration** (`runtime/sprint_scheduler.py`):
- `_prewarm_hermes_for_sprint()`: advisory QuantizationSelector check before load
- Logs selected budget: quantization, max_tokens, max_latency_ms, reason
- Actual load authority stays in ModelManager (which calls QuantizationSelector)

**Key invariants**:
- Model lifecycle authority stays in brain modules (F203J-1)
- Governor denies → model load skipped (F203J-2)
- No operation > 1.5GB RSS except governed model load (F203J-3)
- Fallback `Q4_K_M` on any error (F203J-4)
- No automatic model download in tests (F203J-5)
- JS renderer blocked under model load — via F202H transport policy

**Quantization policy table**:

| UMA state | Free UMA GiB | io_only | swap | Selected quantization | max_tokens | max_latency_ms |
|-----------|-------------|---------|------|----------------------|------------|----------------|
| CRITICAL  | any         | any     | any  | Q4_K_M               | 512        | 30000          |
| EMERGENCY | any         | any     | any  | Q4_K_M               | 512        | 30000          |
| WARN      | < 1.5       | any     | any  | Q4_K_M               | 512        | 30000          |
| WARN      | >= 1.5      | any     | any  | Q5_K_M               | 1024       | 45000          |
| OK        | < 1.5       | any     | any  | Q4_K_M               | 512        | 30000          |
| OK        | >= 1.5, < 2.5 | any  | any  | Q5_K_M               | 1024       | 45000          |
| OK        | >= 2.5      | False   | False| Q8_0                 | 2048       | 60000          |

**Tests**: `tests/probe_f203j/test_quantization_selector.py` — 20 probe tests (F203J-1 through F203J-20)

---

## F204J: Enforced M1 Mission Budget (2026-04-26)

**Přidáno** — enforceable M1 8GB budget across sidecar bus, embedding fallback, renderer/model guards, and benchmark. Peak RSS without model <= 5.5 GiB.

**Constants** (`runtime/resource_governor.py`):
- `MISSION_PEAK_RSS_GIB = 5.5` — hard ceiling for peak RSS
- `SIDECAR_DEFAULT_ESTIMATE_MB = 128` — default MB estimate per sidecar
- `HEAVY_SIDECARS = ("embedding", "wayback_diff", "social_identity", "rir_correlation")`
- `MAX_BUDGET_EVENTS = 100`

**SidecarAdmission dataclass** (`runtime/resource_governor.py`):
- `allowed: bool`, `sidecar_name: str`, `reason: str`, `rss_gib: float`, `uma_state: str`, `estimated_mb: int`
- Returned by `M1ResourceGovernor.sidecar_admission()`

**MissionBudgetSnapshot dataclass** (`runtime/resource_governor.py`):
- `sprint_id`, `peak_rss_gib`, `peak_uma_used_gib`, `sidecars_skipped`, `model_loaded`, `renderer_allowed`, `fetch_limit`

**M1ResourceGovernor.sidecar_admission()**:
- Checks if a sidecar can be admitted given current memory state
- Blocks heavy sidecars when UMA is CRITICAL/EMERGENCY
- Blocks heavy sidecars when `high_water >= 0.85`
- Blocks heavy sidecars when `rss_gib > MISSION_PEAK_RSS_GIB - 0.5`
- Returns `SidecarAdmission` with `allowed` flag and `reason`
- Fail-soft: returns `allowed=True` if any check fails

**SidecarBus integration** (`runtime/sidecar_bus.py`):
- `_is_heavy_blocked()` now returns `(blocked: bool, reason: str)` tuple
- Uses `governor.sidecar_admission()` for consistent admission checks
- Records skipped reason in `SidecarRunResult.skipped_reason`

**StreamingEmbedder fallback** (`intelligence/streaming_embedder.py`):
- `_embed_fallback()` now chunks input just like the normal path
- Never materializes entire sprint in one batch — respects `MAX_EMBEDDING_BATCH=16`
- Fallback still used when model cannot be loaded

**SprintSchedulerResult budget fields** (`runtime/sprint_scheduler.py`):
- `sidecars_skipped: tuple[str, ...]` — heavy sidecars skipped due to RAM pressure
- `peak_rss_gib: float` — peak RSS observed during sprint
- `budget_violations: int` — count of times RSS exceeded MISSION_PEAK_RSS_GIB

**SprintScheduler budget tracking** (`runtime/sprint_scheduler.py`):
- `SidecarDispatcher` (F205F) tracks skipped sidecar names via `result_sink.sidecars_skipped`
- `_sidecar_dispatcher: SidecarDispatcher` — extracted dispatch bookkeeping (F205F)
- `_peak_rss_gib: float` — tracks peak RSS across cycles
- `_run_resource_governor_advisory()` at TEARDOWN: samples RSS, records violations, sets result fields
- Sidecar results from `run_all_sidecars()` are scanned for skipped heavy sidecars

**Hermetic benchmark** (`benchmarks/m1_phase4_budget.py`):
- `--hermetic` flag (default True) — no network, no MLX inference
- Measures: peak RSS, sidecar admission checks, embedding fallback chunking
- Pass condition: `peak_rss_gib <= 5.5`
- Writes bounded summary to stdout and optional JSON

**Key invariants**:
- `MISSION_PEAK_RSS_GIB = 5.5` hard ceiling — not configurable at runtime
- Model path only through quantization selector and lifecycle (F202J/F203J authority)
- RAM guard: skip heavy sidecar if RSS > high_water or UMA critical/emergency
- Each collection has MAX_* constant
- Fail-soft: budget sampler failure → safe degraded mode, never crashes sprint

**Tests**: `tests/probe_f204j/test_m1_mission_budget.py` — 25 probe tests (F204J-1 through F204J-11)

---

## F202F: Local Graph/RAG Analyst Workbench (2026-04-24)

**Přidáno** — analyst read-side facade for local questions over findings, graph, and vectors. Works without LLM (extractive fallback) and without external network calls.

**AnalystWorkbench** (`knowledge/analyst_workbench.py`):
- `AnalystWorkbench` — read-side facade aggregating DuckDBShadowStore, DuckPGQGraph, LanceDB VectorStore
- `AnalystAnswer` — result DTO with extractive_answer, llm_answer, evidence_pointers, related_entities, context_bytes, model_used, sources_used, timing_ms
- `EvidencePointer` — finding_id, source_type, query, confidence, ts, provenance, envelope_available, snippet
- `RelatedEntity` — entity_value, entity_type, confidence, hops, relation_types

**Bounds** (F202F-1 through F202F-5):
- `MAX_CONTEXT_BYTES = 8192` — 8KB max context per answer
- `MAX_TOP_K = 20` — max results from any single source
- `MAX_GRAPH_HOPS = 2` — entity history max hops
- `MAX_EVIDENCE_PTRS = 5` — max evidence pointers per answer
- `MAX_RELATED_ENTITIES = 10` — max related entities per answer

**Core pipeline** (`ask()`):
1. `query_findings()` — keyword search over recent DuckDBShadowStore findings
2. `query_graph()` — multi-hop entity traversal via DuckPGQGraph
3. `_extract_answer()` — deterministic extractive text (no model required)
4. `get_evidence_pointers()` — EvidencePointer list from findings
5. `get_related_entities()` — RelatedEntity list from graph traversal
6. (Optional) `_generate_llm_answer()` via `brain/model_lifecycle.py`

**No-model invariants** (F202F-12, F202F-16):
- `_extract_answer()` returns keyword-matched paragraph — pure Python, no MLX
- No external network calls — all data sources are local (DuckDB, LanceDB, DuckPGQGraph)
- Model lifecycle via `brain.model_lifecycle.load_model()` / `unload_model()` only
- `ask()` always returns `extractive_answer` even when `model_used=False`

**Export layer** (`export/jsonld_exporter.py`):
- `render_analyst_evidence_jsonld()` — exports AnalystAnswer as JSON-LD with ghost: namespace
- `render_analyst_evidence_jsonld_str()` — deterministic JSON string with sorted keys
- Evidence pointers include: finding_id, source_type, query, confidence, ts, provenance, snippet

**Store wiring** (pass-through to existing modules):
- DuckDBShadowStore: `async_query_recent_findings(limit)` → finding dicts
- DuckPGQGraph: `find_entity_history(entity, max_hops)` → entity history
- LanceDB VectorStore: `query(vector, k, index_type="text")` → (id, score) tuples
- SemanticStore: `semantic_pivot(query, top_k)` → finding_ids (if available)

**Factory**: `create_analyst_workbench()` — lazily resolves VectorStore and DuckPGQGraph singletons; DuckDBShadowStore and SemanticStore passed explicitly by caller.

**Tests**: `tests/probe_f202f/test_local_graph_rag_workbench.py` — 50 tests:
- F202F-1 through F202F-5: bounds constants
- F202F-6: `ask()` always returns extractive_answer
- F202F-7: `query_findings()` keyword search
- F202F-8: `query_graph()` bounded RelatedEntity list
- F202F-9: `query_vectors()` bounded by MAX_TOP_K
- F202F-10: evidence_pointers built from findings
- F202F-11: related_entities from graph traversal
- F202F-12: `_extract_answer()` no-model fallback
- F202F-13: `_truncate_to_bytes()` respects MAX_CONTEXT_BYTES
- F202F-14: `_build_evidence_pointers()` caps at MAX_EVIDENCE_PTRS
- F202F-15: `create_analyst_workbench()` factory
- F202F-16: no external network calls
- F202F-17: JSON-LD evidence export
- F202F-18: `ask_sync()` produces AnalystAnswer
- F202F-19: graph_rag `multi_hop_search` has max_nodes bounded
- F202F-20: rag_engine has no-model BM25/hybrid fallback

---

## F202G: Hypothesis-Driven Pivot Planner (2026-04-24)

**Přidáno** — bounded advisory layer that generates next pivots from accepted findings and envelope facets. Scheduler uses pivots as advisory ordering input, NOT as new sprint owner.

**PivotPlanner** (`runtime/pivot_planner.py`):
- `Pivot` — dataclass with priority, pivot_type, ioc_value, ioc_type, reason, expected_value, source_hint, evidence_pointers
- `PivotType` — constants: DOMAIN, IDENTITY, LEAK, ARCHIVE, GRAPH
- `PivotPlanner.plan_pivots(findings, graph_stats, max_pivots)` → `list[Pivot]`

**Pivot types** (5 total):
- `domain` — DNS, WHOIS, passive DNS pivots from domain IOCs
- `identity` — entity resolution, profile correlation from email/username IOCs
- `leak` — paste/GitHub/breach signal pivots from email IOCs
- `archive` — wayback, archive.org historical pivots from domain/URL IOCs
- `graph` — IOC graph traversal pivots from IP/hash IOCs

**Bounds** (F202G-1 through F202G-4):
- `MAX_PIVOTS = 20` — max pivots per sprint
- `MAX_ENVELOPE_SIZE` — envelope deserialization bounded
- Graph stats optional — no graph means no novelty bonus
- Deduplication key: `(pivot_type, ioc_type, ioc_value)` — same IOC can have multiple pivot types

**Scoring**:
- `_cheap_score_finding()` — heuristic scoring without model inference
- Source type quality boost for ct_log, certificate, cisa_kev, threatfox_ioc, public, deep_probe, forensics, multimodal
- Signal facets boost from envelope when available
- Per-type scoring: domain (novelty bonus), identity (email/URL boost), leak (email breach bonus), archive (supplementary), graph (novelty + degree)

**Evidence envelope integration**:
- `_deserialize_envelope()` — extracts envelope from finding.payload_text if audit_reason present
- Envelope signal_facets influence scoring
- `source_hint` tracks which finding triggered each pivot

**Discovery wiring** (`discovery/source_registry.py`):
- `PIVOT_TYPE_MAP` — maps IOC types to default pivot types
- `get_pivot_type(ioc_type)` → pivot_type string
- `get_pivot_task_types(pivot_type)` → list of task types for each pivot

**Scheduler advisory hook** (`runtime/sprint_scheduler.py`):
- `_run_pivot_planner_advisory()` — called at sprint teardown
- `inject_pivot_planner(planner)` — DI interface for PivotPlanner
- Advisory only — scheduler retains authority, pivots influence ordering not execution
- Fail-soft — planner failure never blocks export or sprint

**Tests**: `tests/probe_f202g/test_hypothesis_pivot_planner.py` — 20 tests:
- F202G-1: MAX_PIVOTS=20 bound enforced
- F202G-2: Empty findings returns empty list (fail-soft)
- F202G-3: Domain IOC → domain pivot + archive pivot
- F202G-4: IP IOC → domain pivot (reverse DNS) + graph pivot
- F202G-5: Hash IOC → graph pivot
- F202G-6: Email IOC → leak pivot + identity pivot
- F202G-7: URL IOC → domain pivot + archive pivot
- F202G-8: Envelope deserialization from payload_text
- F202G-9: Pivot has required fields (reason, expected_value, source_hint, evidence_pointers)
- F202G-10: Deduplication by (pivot_type, ioc_type, ioc_value)
- F202G-11: Sorting by expected_value descending
- F202G-12: Planner fail-soft on exception
- F202G-13: PIVOT_TYPE_MAP in source_registry
- F202G-14: get_pivot_task_types returns task list

**Constraints honored**:
- Planner failure never blocks export or sprint (fail-soft)
- Model load/unload only via brain.model_lifecycle (future: _score_with_model async stub exists)
- No model + JS renderer concurrently (advisory only, no rendering)

---

## F203G: Hypothesis Feedback Loop & Dead-End Pruning (2026-04-26)

**Přidáno** — PivotPlanner learns from historical yield of pivot types per target, penalizing low-yield types to reduce blind pivot branching.

**Schema** (`knowledge/duckdb_store.py`):
```sql
CREATE TABLE IF NOT EXISTS hypothesis_feedback (
    id TEXT PRIMARY KEY,
    target_id TEXT,
    pivot_type TEXT,
    ioc_type TEXT,
    produced_count INTEGER,
    accepted_count INTEGER,
    signal_value DOUBLE,
    ts DOUBLE
);
```

**New modules**:
- `runtime/hypothesis_feedback.py` — `HypothesisFeedbackRecord`, `HypothesisFeedbackSummary`, `HypothesisFeedbackAdapter`
- `DuckDBShadowStore.async_record_hypothesis_feedback()` / `async_get_hypothesis_feedback()`

**Bounds** (F203G-1 through F203G-3):
- `MAX_FEEDBACK_RECORDS = 10000` — hard cap on stored feedback records
- `MAX_PRUNED_TYPES = 20` — max pivot types that can be penalized
- No hard ban: penalty only after `consecutive_zero_yield >= 3` (soft penalty, multiplier min 0.1)

**Feedback flow**:
1. `SprintScheduler.record_hypothesis_feedback()` records outcomes via `duckdb.async_record_hypothesis_feedback()`
2. `HypothesisFeedbackAdapter.async_get_summary()` aggregates records into per-(pivot_type, ioc_type) summaries
3. `_run_pivot_planner_advisory()` reads summary from duckdb and passes to `PivotPlanner.plan_pivots(feedback_summary=...)`
4. `_get_feedback_penalty()` applies penalty multiplier to each pivot's `expected_value`

**Penalty rules**:
- `avg_signal >= 0.3` → multiplier = 1.0 (no penalty)
- `consecutive_zero_yield >= 3` → multiplier = max(0.1, 0.5 - (zeros - 3) * 0.1)
- `avg_signal < 0.1` with no zeros → multiplier = 0.7

**Tests**: `tests/probe_f203g/test_hypothesis_feedback_loop.py` — 17 tests:
- F203G-1: MAX_FEEDBACK_RECORDS=10000 bound
- F203G-2: MAX_PRUNED_TYPES=20 bound
- F203G-3: HypothesisFeedbackRecord frozen dataclass
- F203G-4: HypothesisFeedbackSummary frozen dataclass
- F203G-5: Adapter in-memory mode (no store → no-op)
- F203G-6: Penalty multiplier no-penalty (avg_signal >= 0.3)
- F203G-7: Penalty multiplier consecutive zero (>= 3 → applied)
- F203G-8: Penalty minimum is 0.1
- F203G-9: Mild low-signal penalty (0.7)
- F203G-10: Unknown type → multiplier=1.0
- F203G-11: Adapter _aggregate groups by (pivot_type, ioc_type)
- F203G-12: DuckDB schema has hypothesis_feedback table
- F203G-13: async_record_hypothesis_feedback method exists
- F203G-14: async_get_hypothesis_feedback method exists
- F203G-15: plan_pivots accepts feedback_summary parameter
- F203G-16: plan_pivots penalizes low-yield pivot type
- F203G-17: plan_pivots no penalty for unknown type

**Dependencies**: F202G (PivotPlanner), F203A (target_id per sprint)
**Dependents**: Benefits F203G-adjacent sprint planning

---

## F202H: OPSEC Transport Policy Engine (2026-04-24)

**Přidáno** — single read-side policy engine for transport posture. Prevents M1 model+renderer conflicts and provides concurrency/timeout hints.

**OPSEC Policy Engine** (`runtime/opsec_policy.py`):
- `OPSECContext` — dataclass with: `has_model_context`, `has_stealth`, `transport_hint`, `risk_level`
- `RendererPolicy` — frozen dataclass: `allowed`, `max_concurrent`, `timeout_hint`, `blocked_reason`
- `ConcurrencyHint` — frozen dataclass: `max_workers`, `timeout_s`
- `TransportPolicy` — composite: `renderer` + `concurrency` + `transport` string

**Core functions**:
- `get_renderer_policy(ctx)` — M1 model+renderer conflict guard. Returns `allowed=False` when `has_model_context=True` (blocked_reason="M1_model_context_active"). Also blocks when renderer concurrency exhausted.
- `get_concurrency_hint(transport_hint)` — per-transport concurrency hints: clearnet=3, tor=2, i2p=1, stealth=2
- `get_transport_policy(ctx)` — composite policy combining renderer + concurrency. Lowers concurrency when renderer blocked.
- `get_stealth_capability_flags(has_model_context)` — advisory flags for StealthSession. Disables TLS fingerprint under model load to reduce RAM pressure.

**Renderer lifecycle tracking**:
- `acquire_renderer_slot()` / `release_renderer_slot()` — thread-safe slot management
- `get_renderer_active_count()` — current active renderer count
- MAX_CONCURRENT_RENDERERS=1 (M1 single-JS-renderer constraint)

**Transport resolver integration** (`transport/transport_resolver.py`):
- `get_transport_hint_string(url)` — maps Transport enum to string ("clearnet", "tor", "i2p") for opsec_policy
- Aligns with existing `get_transport_for_url()` — same classification logic

**Public fetcher integration** (`fetching/public_fetcher.py`):
- `_fetch_with_camoufox()` now uses `get_renderer_policy(OPSECContext(has_model_context=is_embedding_context_active()))` instead of inline check
- Policy consulted before every Camoufox launch — centralized M1 conflict guard

**Key invariants**:
- [F202H-I1] `get_renderer_policy(OPSECContext(has_model_context=True)).allowed` is False
- [F202H-I2] `get_concurrency_hint("clearnet").max_workers` == 3
- [F202H-I3] `acquire/release` slot maintains count within [0, MAX_CONCURRENT_RENDERERS]
- [F202H-I4] `get_stealth_capability_flags(has_model_context=True)["tls_fingerprint"]` is False
- [F202H-I5] `get_transport_policy` returns `TransportPolicy` with correct `renderer.allowed`
- [F202H-I6] `asyncio.gather(return_exceptions=True)` + `_check_gathered` pattern present
- [F202H-I7] smoke: renderer disabled while model context active, fail-open to clearnet

**Fail-safe**: All methods return safe defaults — renderer allowed=False with reason when blocked, concurrency hints are conservative. No crashes on import failure (fail-open).

**Tests**: `tests/probe_f202h/test_opsec_transport_policy.py` — 28 tests all passing.

---

## Architectural verdict

### What is real today
- The project already has a real, typed canonical sprint path with feed + public + CT + aggressive branch fan-out + partial export + Hermes prewarm.
- Public pipeline is the densest integration surface: CommonCrawl, onion discovery, academic discovery, paste monitoring, GitHub secret scanning, bounded P12 hypothesis/ToT burst.
- The store seam is strong and centralized.

### What is not real today
- No `SprintMode` enum.
- No root `--mode aggressive` CLI.
- `deep_research/` is now canonical (F197A): `run_deep_probe_if_enabled()` -> findings normalized to `CanonicalFinding` -> `async_ingest_findings_batch()`
- No evidence that many ambitious subtrees (`forensics`, `multimodal`, `planning`, `research`) are part of the canonical sprint path.
- `rl/marl_coordinator.py` was deleted in F196A (stub with zero production call-sites); `rl/sprint_policy_manager.py` is the surviving RL plane.
- Ghost modules (`intelligence_dispatcher`, `memory_watchdog`, `session_authority`) were removed in F196A; sprint_scheduler.py is clean.
- Test baseline is not green; there is still real debt before major new subsystem activation.

## F202A Evidence Envelope and Signal Schema (2026-04-24)

**Přidáno** — bounded evidence envelope layer for CanonicalFinding audit trail:

**Schema** (`knowledge/finding_envelope.py`):
- `FindingEnvelope` plain Python class with fields: `audit_reason: str`, `evidence_pointers: list[str]`, `signal_facets: dict[str, float]`, `suggested_pivots: list[dict]`
- `MAX_ENVELOPE_SIZE = 4098` bytes — deterministic upper bound
- `envelope_size_guard()` — returns True if serialized JSON fits within bound
- `serialize_envelope()` / `deserialize_envelope()` — roundtrip to/from JSON string

**Storage seam** (`knowledge/duckdb_store.py`):
- Envelope serialized into `CanonicalFinding.payload_text` JSON field — no new write path
- `async_ingest_findings_with_envelope()` — ingest with envelope; size guard degrades oversized to plain
- `async_get_findings_with_envelope()` — read seam with deserialized envelope attached

**Export** (`export/sprint_exporter.py`):
- `envelope_findings` key in export result dict — list of findings with deserialized envelopes

**Markdown rendering** (`export/sprint_markdown_reporter.py`):
- `_render_envelope_findings()` helper — renders audit_reason, evidence_pointers, signal_facets, suggested_pivots; bounded at 10 findings; fail-soft skips invalid envelopes

**Probe tests**: `tests/probe_f202a/test_evidence_envelope_schema.py` — 19 tests, all passing.

## F202B Identity Stitching Sidecar (2026-04-24)

**Přidáno** — deterministic entity extraction and identity stitching as a bounded sidecar on accepted findings:

**Entity Signal Extractor** (`intelligence/entity_signal_extractor.py`):
- `extract_entities_from_finding()` — extracts emails, usernames, domain handles from `CanonicalFinding.payload_text` via regex
- `extract_entities_from_findings()` — groups entities into `EntitySignalProfile` list; bounded at MAX_PROFILES=500
- `ExtractedEntity` dataclass: entity_type, value, raw_value, platform, finding_id, confidence
- `EntitySignalProfile` dataclass: id, primary_name, emails, usernames, domain_handles, platforms, finding_ids, confidence
- No ML models — pure regex/string heuristics
- M1 8GB safe: bounded profile count, no large in-memory structures

**Identity Stitching Canonical Adapter** (`intelligence/identity_stitching_canonical.py`):
- `IdentityStitchingAdapter` wraps `IdentityStitchingEngine` with M1-safe bounds
- `extract_and_stitch()` — converts EntitySignalProfile → IdentityProfile → IdentityStitchingEngine → IdentityCandidate list
- `to_derived_findings()` — converts IdentityCandidate list → CanonicalFinding(source_type="identity_stitching") list
- `upsert_identity_edges()` — writes same_identity edges to graph_service
- Bounded: MAX_COMPARISONS=2000 cap enforced, `optimize_memory()` called after each batch
- All methods fail-soft: sprint continues on any error
- `IdentityCandidate` dataclass: candidate_id, profile_ids, primary_name, emails, usernames, platforms, confidence, signals, evidence, finding_ids

**Graph Service** (`knowledge/graph_service.py`):
- `upsert_identity_edge(src, dst, confidence, evidence)` — convenience wrapper around `upsert_relation` with `rel_type="same_identity"`. Advisory only.

**Sprint Scheduler Hook** (`runtime/sprint_scheduler.py`):
- `_identity_adapter` field (lazy, None until first use)
- `_run_identity_stitching_sidecar(findings, store, query)` — async sidecar after CT findings are accepted and stored
  - Extracts entity profiles (bounded MAX_PROFILES=500)
  - Runs stitching (bounded MAX_COMPARISONS=2000)
  - Upserts graph edges (advisory, fail-soft)
  - Converts to derived CanonicalFinding list
  - Ingests via `async_ingest_findings_batch()`
- Called after `_run_ct_log_discovery_in_cycle()` stores findings — non-blocking sidecar
- `identity_candidates_found` and `identity_findings_produced` counters in `SprintSchedulerResult`
- `core/__main__.py`: scorecard includes `identity_candidates_found` and `identity_findings_produced`

**Markdown Rendering** (`export/sprint_markdown_reporter.py`):
- `_render_identity_candidates()` — renders candidate_id, primary_name, confidence (high/medium/low), platforms, emails, usernames, signals, evidence, finding_ids
- Bounded at 10 candidates displayed
- Graceful degradation: skips non-dict items, empty candidates

**Data contracts**:
- Derived findings have `source_type="identity_stitching"` — searchable in DuckDB
- Identity candidates are explainable: each has confidence + individual signals + evidence pointers
- Graph edges are advisory (same_identity rel_type) — do not affect sprint completion
- No new public APIs beyond existing `async_ingest_findings_batch()`

**M1 8GB bounds**:
- MAX_PROFILES=500 entity profiles per sprint (from entity_signal_extractor)
- MAX_COMPARISONS=2000 stitching comparisons per sprint (hard cap)
- `optimize_memory()` called after each stitching batch

**Tests**: `tests/probe_f202b/test_identity_stitching_canonical.py` — 9 test classes, all MagicMock/AsyncMock, no real DB deps:
- F202B-1: Entity extraction (emails, usernames, domain handles)
- F202B-2: Profile grouping and MAX_PROFILES bound
- F202B-3: Adapter factory and stats
- F202B-4: extract_and_stitch produces candidates
- F202B-5: to_derived_findings produces CanonicalFinding
- F202B-6: upsert_identity_edge delegates to upsert_relation
- F202B-7: SprintSchedulerResult has identity counters
- F202B-8: Markdown rendering with confidence/signals
- F202B-9: SprintScheduler has _identity_adapter field

## F203B Attribution Confidence Scorer (2026-04-25)

**Přidáno** — explainable confidence scoring for identity stitching candidates using multiple attribution factors. No model load, no network, pure Python with Levenshtein fallback.

**AttributionConfidenceScorer** (`intelligence/attribution_scorer.py`):
- `score_pair(left, right, context)` → `AttributionScore` with confidence 0.0-1.0, factors, evidence_ids, factor_weights
- `score_candidates(candidates)` → dict of `"left_id|right_id" → AttributionScore`
- `get_factor_breakdown(score)` → human-readable factor analysis
- `MAX_FACTOR_COMPARISONS=5000` hard cap, `comparison_count` property

**AttributionScore** dataclass (frozen=True):
- `confidence: float` — final weighted score 0.0-1.0
- `factors: Tuple[AttributionFactor, ...]` — contributing factor details
- `evidence_ids: Tuple[str, ...]` — audit trail of factor_ids
- `factor_weights: Dict[str, float]` — weights used for reproducibility

**AttributionFactor** dataclass (frozen=True):
- `factor_id`, `factor_type`, `raw_score`, `weighted_score`, `evidence`, `metadata`

**Five Attribution Factors** (weighted, sum=1.0):
| Factor | Weight | Description |
|--------|--------|-------------|
| `email_domain_match` | 0.25 | Exact domain or shared TLD |
| `username_pattern_similarity` | 0.20 | Levenshtein similarity ≥0.6 |
| `temporal_overlap` | 0.20 | Jaccard ≥0.3 on shared finding_ids |
| `shared_infrastructure` | 0.20 | Shared platform presence |
| `pgp_key_correlation` | 0.15 | Matching PGP fingerprint in evidence |

**Integration Points**:
- `IdentityStitchingAdapter.score_and_enrich_candidates(candidates, scorer)` — post-processes candidates, adds `attribution_confidence` and `attribution_factor_types` to signals
- `upsert_identity_edges()` — now uses `attribution_confidence` from signals as edge weight (fallback: `cand.confidence`)
- `_run_identity_stitching_sidecar()` — calls `score_and_enrich_candidates()` after stitching, before graph upsert
- `to_derived_findings()` — sets `source_type="identity_attribution"` for enriched candidates, uses attribution confidence as finding confidence
- Markdown reporter — shows `Attribution Confidence`, `Attribution Factors`, and `Base Confidence` in identity section

**Guardrails**:
- No model load; no network I/O
- Fail-soft: empty scores returned on any error
- Canonical persist via `async_ingest_findings_batch()`
- Confidence clamped to [0.0, 1.0]
- Pure Python Levenshtein fallback (no rapidfuzz dependency)

**Tests**: `tests/probe_f203b/test_attribution_confidence_scorer.py` — 41 probe tests covering all factors, fail-soft behavior, and enrichment integration.

## F202C Asset Exposure Correlator (2026-04-24)

**Přidáno** — correlates asset exposure signals into explainable exposure findings:

**Signal Sources Consumed** (`intelligence/exposure_correlator.py`):
- `ct_log` findings: cert→SAN mappings, issuers, timestamps → `SIGNAL_TYPE_CT_CERT`
- `open_storage` findings: exposed S3/Firebase/Elasticsearch/MongoDB buckets → `SIGNAL_TYPE_OPEN_BUCKET`
- `jarm` findings: TLS fingerprint hashes → `SIGNAL_TYPE_JARM`
- `passive_dns` findings: domain→IP mappings → `SIGNAL_TYPE_PASSIVE_DNS`

**Correlation Types Produced**:
- `exposed_host`: host with open bucket + cert-domain or DNS correlation
- `cert_domain_relation`: CT cert SAN with issuer and timestamp
- `open_bucket`: confirmed exposed cloud storage bucket (S3/Firebase/Elasticsearch/MongoDB)
- `suspicious_service_fingerprint`: JARM hash with GREASE/000 prefix (known-suspicious)
- `infra_cluster`: 2+ hosts sharing same JARM hash (co-located infrastructure)

**ExposureCorrelatorAdapter** (`intelligence/exposure_correlator.py`):
- `correlate(findings, query)` — entry point, returns `CanonicalFinding(source_type="exposure_correlation")` list
- `get_stats()` / `reset()` — stats tracking for probe verification
- `create_exposure_correlator_adapter()` — factory function

**Evidence Envelope Fields** (in `payload_text` JSON):
- `evidence_pointers`: list of source `finding_id`s
- `signal_facets`: `{signal_type: confidence}` per-signal confidence contribution
- `suggested_pivots`: recommended follow-up queries as `[{type, query}, ...]`
- `correlation_payload`: full correlation data (jarm_hash, host_count, bucket_type, etc.)

**Sprint Scheduler Hook** (`runtime/sprint_scheduler.py`):
- `_exposure_adapter` field (lazy, None until first use)
- `_run_exposure_correlator_sidecar(findings, store, query)` — async sidecar after CT findings are accepted
  - Correlates signals into `ExposureFinding` objects
  - Converts to `CanonicalFinding` list
  - Ingests via `async_ingest_findings_batch()`
- Called after `_run_identity_stitching_sidecar()` — non-blocking sidecar
- `exposure_findings_produced` and `correlated_assets_count` counters in `SprintSchedulerResult`

**Bounds**:
- `MAX_ASSETS = 1000` — max unique assets per sprint (hard cap)
- `MAX_SIGNALS_PER_ASSET = 3` — max signals per asset (hard cap)
- `MAX_FINDINGS = 500` — max exposure findings produced (hard cap)
- All methods fail-soft: sprint continues on any error

**Data Contracts**:
- Derived findings have `source_type="exposure_correlation"` — searchable in DuckDB
- Correlation findings are explainable: each has confidence rationale + evidence pointers
- `asyncio.gather(return_exceptions=True)` + `_check_gathered()` for all async operations
- Persists only via `async_ingest_findings_batch()` (canonical write path)
- External calls (JARM, open_storage scan) have timeouts + graceful fallback

**M1 8GB Safety**:
- Bounded asset map (MAX_ASSETS=1000) prevents OOM
- Signal-per-asset cap (MAX_SIGNALS_PER_ASSET=3) limits correlation explosion
- No ML models — pure regex/string heuristics

---

### F202D: Leak and Secret Sentinel

**Module**: `intelligence/leak_sentinel.py`

**Role**: Bounded optional branch that converts paste/GitHub/breach signals into redacted CanonicalFinding objects. No raw secrets in findings — all masked via redaction patterns.

**Sources**:
- `data_leak_hunter`: breach API results (HaveIBeenPwned, DeHashed, etc.)
- `pastebin_monitor`: paste site scraping (pastebin, paste.gg, rentry)
- `github_secret_scanner`: GitHub code search for leaked secrets

**Signal types produced**:
- `paste_leak`: paste site findings with redacted secrets
- `github_secret`: GitHub secret findings with masked context
- `leak_sentinel`: breach database findings with redacted PII

**Redaction** (F202D-3):
- Secret patterns applied BEFORE `fallback_sanitize` to prevent partial masking
- AWS keys (`AKIA...`), Stripe keys (`sk_live_...`), Bearer tokens, private key headers
- Generic credentials via `api_key=`, `password=`, `secret=`, `token=` patterns
- Google API keys (`AIza...`)

**Evidence envelope** (F202D-2):
- Stored in `payload_text` alongside finding data
- Contains: `audit_reason`, `evidence_pointers`, `signal_facets`, `suggested_pivots`
- All secrets replaced with `[REDACTED]` tokens before envelope construction

**Sprint Scheduler Hook** (`runtime/sprint_scheduler.py`):
- `_leak_sentinel_adapter` field (lazy, None until first use)
- `_run_leak_sentinel_sidecar(findings, store, query)` — async sidecar after CT findings accepted
  - Runs bounded leak scans via `LeakSentinelAdapter.scan(query)`
  - Converts findings to `CanonicalFinding` list
  - Ingests via `async_ingest_findings_batch()`
- Called after `_run_exposure_correlator_sidecar()` — non-blocking sidecar
- `leak_findings_produced` counter in `SprintSchedulerResult`

**Bounds** (F202D-4):
- `MAX_LEAK_SOURCES = 3` — max concurrent source fetches
- `MAX_FINDINGS_PER_SOURCE = 50` — max findings per source
- `MAX_TOTAL_FINDINGS = 100` — max total findings (hard cap)
- `TIMEOUT_PER_SOURCE = 30.0` — seconds per source fetch

**Constraints**:
- No raw secrets in report/export — all redacted before persistence
- External calls timeout + fail-soft
- No background monitoring loop — single-shot bounded execution
- Persist only via `async_ingest_findings_batch()` (canonical write path)

**Tests**: `tests/probe_f202c/test_asset_exposure_correlator.py` — 12 test classes, all MagicMock, no real DB deps:
- F202C-1: Signal extraction (ct_log, open_storage, jarm, passive_dns)
- F202C-2: Correlation — open_bucket
- F202C-3: Correlation — exposed_host (bucket + cert/dns)
- F202C-4: Correlation — cert_domain_relation
- F202C-5: Correlation — infra_cluster (JARM clustering)
- F202C-6: CanonicalFinding conversion with evidence envelope
- F202C-7: Bounds degradation (MAX_ASSETS, MAX_SIGNALS_PER_ASSET, MAX_FINDINGS)
- F202C-8: Public API — correlate_exposure_signals
- F202C-9: ExposureCorrelatorAdapter stats and reset
- F202C-10: Suspicious JARM fingerprints (GREASE/000 prefix)
- F202C-11: Stats tracking
- F202C-12: Evidence envelope fields completeness

## F203A: Sprint Diff Engine & Target Profile Persistence (2026-04-25)

**Phase 3 seam — cross-sprint memory for target analytics.**

**Role**: Analyst-visible diff: what is new, disappeared, or changed compared to previous sprints of the same target. Enables temporal tracking of entity evolution across sprint runs.

**Dependencies**: F202A (evidence envelope), F202B (entity extraction).

**Module**: `knowledge/sprint_diff_engine.py`

**API**:
```python
@dataclass(frozen=True)
class SprintDiffResult:
    target_id: str
    current_sprint_id: str
    previous_sprint_id: str | None
    new_findings: list[dict]        # bounded MAX_DIFF_FINDINGS=100
    disappeared_findings: list[dict]
    changed_entities: list[dict]

@dataclass
class TargetProfileSummary:
    target_id: str
    first_seen: float
    last_seen: float
    cumulative_finding_count: int
    entity_summary_json: str         # JSON: {total, by_type, by_source}
    finding_velocity: float = 0.0
    entity_types: dict[str, int] = field(default_factory=dict)
```

**Diff logic**:
- Entity key = `(ioc_type::ioc_value)` — case-insensitive composite
- `new` = current entities NOT in previous sprint
- `disappeared` = previous entities NOT in current sprint
- `changed` = same `ioc_value` but different `ioc_type` or `finding_id`

**Profile logic**:
- `first_seen` = min(previous.first_seen, current_ts) — oldest first-seen across sprints
- `cumulative_finding_count` = previous + len(current)
- `finding_velocity` = cumulative / max(days_since_first_seen, 1)
- `entity_summary_json` = `{total, by_type:{}, by_source:{}}` — capped at MAX_PROFILE_ENTRIES=500 per bucket

**DuckDB schema** (`knowledge/duckdb_store.py`):
```sql
CREATE TABLE IF NOT EXISTS target_profiles (
    target_id TEXT PRIMARY KEY,
    first_seen DOUBLE,
    last_seen DOUBLE,
    cumulative_finding_count INTEGER,
    entity_summary_json TEXT
);
```
Methods: `ensure_target_profiles_schema()`, `async_upsert_target_profile()`, `async_get_target_profile()`, `async_get_previous_findings_for_target()`.

**Sprint Scheduler Hook** (`runtime/sprint_scheduler.py`):
- `_run_sprint_diff_sidecar(findings, store, query)` — async sidecar after accepted findings are stored
  - Reads previous findings via `store.async_get_previous_findings_for_target(target_id)`
  - Computes diff via `SprintDiffEngine.compute_diff()`
  - Ingest diff findings via `async_ingest_findings_batch()` with `source_type="sprint_diff"`
  - Updates target profile via `store.async_upsert_target_profile()`
- `sprint_diff_findings_produced` counter in `SprintSchedulerResult`
- Wired after `_run_evidence_triage_sidecar()` in the CT log sidecar chain

**Markdown Rendering** (`export/sprint_markdown_reporter.py`):
- `_render_sprint_diff_section()` — bounded display of 20 diff findings
- Emoji labels: 🆕 NEW / ❌ GONE / ⚡ CHANGED
- Fields: ioc_value, target_id, ioc_type, previous→current sprint

**Exporter** (`export/sprint_exporter.py`):
- Queries duckdb for `source_type="sprint_diff"` findings at export time
- Populates `scorecard["sprint_diff_findings"]` for reporter consumption

**Guardrails**:
- Canonical writes only via `async_ingest_findings_batch()` — no direct writes
- Persistent metadata methods only in `duckdb_store.py` — no new store APIs elsewhere
- Fail-soft throughout: all external calls wrapped in try/except
- No absolute paths; no full payload dumps in reports

## F204D: Target Memory 2.0 — Cross-Sprint Persistent Target State (2026-04-26)

**Phase 3 seam — persistent cross-sprint target state beyond simple target_profiles count.**

**Role**: Expands cross-sprint target memory from entity count to full persistent state: entity facets, exposure facets, ASN/org placeholder, top pivots, confidence drift. Enables temporal tracking of target evolution across sprint runs.

**Dependencies**: F203A (target profiles baseline). Benefits from F202C (exposure correlator) and F202G (hypothesis-driven pivot planner).

**Module**: `knowledge/target_memory.py`

**DuckDB Schema** (`knowledge/duckdb_store.py`):
```sql
CREATE TABLE IF NOT EXISTS target_memory (
    target_id TEXT PRIMARY KEY,
    first_seen_ts DOUBLE NOT NULL,
    last_seen_ts DOUBLE NOT NULL,
    sprint_count INTEGER NOT NULL,
    cumulative_finding_count INTEGER NOT NULL,
    entity_facets_json TEXT NOT NULL,
    exposure_facets_json TEXT NOT NULL,
    pivot_facets_json TEXT NOT NULL,
    confidence_drift_json TEXT NOT NULL,
    updated_by_sprint_id TEXT NOT NULL
);
```
Methods: `async_upsert_target_memory()`, `async_get_target_memory()`.

**Dataclasses** (`knowledge/target_memory.py`):
- `TargetMemory` — frozen dataclass with all 10 schema fields
- `TargetMemoryUpdate` — update payload with entity/exposure/pivot facets
- `TargetMemoryService` — merge logic with RAM guard

**Bounds**:
- `MAX_MEMORY_ENTITIES = 500`
- `MAX_MEMORY_EXPOSURES = 500`
- `MAX_MEMORY_PIVOTS = 100`
- `MAX_MEMORY_JSON_BYTES = 65536`

**Sprint Scheduler Hook** (`runtime/sprint_scheduler.py`):
- `_run_target_memory_update(findings, store, query)` — called after sidecar bus (line ~1677)
  - RAM guard: skip merge if RSS > 85% high_water
  - Extracts entity/exposure/pivot facets from findings
  - Persists via `store.async_upsert_target_memory()`
- `_target_memory_service` instance variable (lazy-init)

**DuckDB ShadowStore Wiring** (`knowledge/duckdb_store.py`):
- Schema in `ensure_target_memory_schema()` (called during init)
- Async helpers: `async_upsert_target_memory()`, `async_get_target_memory()`
- Fail-soft throughout

**Analyst Workbench Read Helper** (`knowledge/analyst_workbench.py`):
- `get_target_memory_summary(target_id)` — returns dict with sprint_count, cumulative_finding_count, entity/exposure/pivot facets, confidence_drift

**Guardrails**:
- Canonical writes only via `async_ingest_findings_batch()` — target_memory helpers are internal to duckdb_store.py
- RAM guard: skip merge under memory pressure (>85% high_water)
- Fail-soft on corrupt JSON → empty facet, not crash
- GHOST_INVARIANTS: gather(return_exceptions=True), _check_gathered(), re-raise CancelledError

### F206H: Explainable Target Drift Intelligence (2026-04-27)

**Cíl**: Rozšířit `confidence_drift` JSON o bounded explainable klíče — `entity_delta`, `exposure_delta`, `pivot_delta`, `drift_reasons` — bez změny DuckDB schema.

**Změny** (`knowledge/target_memory.py`):
- `_compute_confidence_drift()` — nyní vrací navíc `entity_delta`, `exposure_delta`, `pivot_delta`, `drift_reasons`
- `_compute_facet_delta()` — pure Python deterministická funkce pro výpočet key-level delta mezi existing a update facetami; vrací `added`, `removed`, `stable`, `total_prev`, `total_curr`, `top_added`, `top_removed`
- `_compute_drift_reasons()` — generuje bounded seznam drift reason stringů založený na drift_ratio a facet deltas

**Nové klíče v `confidence_drift` dict** (žádné schema změny):
```python
{
    "sprints": int,
    "total_findings": int,
    "avg_findings_per_sprint": float,
    "drift_ratio": float,
    "entity_delta": {  # _compute_facet_delta output
        "added": int,        # new entity types
        "removed": int,      # dropped entity types
        "stable": int,       # types in both
        "total_prev": int,   # capped to MAX_DRIFT_DELTA_KEYS
        "total_curr": int,   # capped to MAX_DRIFT_DELTA_KEYS
        "top_added": list[str],   # up to 5 added keys by score
        "top_removed": list[str], # up to 5 removed keys by score
    },
    "exposure_delta": { /* same structure */ },
    "pivot_delta": { /* same structure */ },
    "drift_reasons": list[str],  # bounded to MAX_DRIFT_REASONS
}
```

**Drift reasons** (příklady):
- `finding_rate_high:ratio=X.XX` — drift_ratio > 1.5
- `finding_rate_low:ratio=X.XX` — drift_ratio < 0.5
- `entity_new_types:N_added` — >5 new entity types
- `entity_dropped_types:N_removed` — >3 dropped entity types
- `entity_expansion:high_churn` — total_curr > 1.5× total_prev
- `entity_contraction:sharp_decline` — total_curr < 0.5× total_prev
- `new_entity:<type>` — top added entity types (až 3)

**Bounds** (F206H):
- `MAX_DRIFT_REASONS = 8` — max drift reason strings
- `MAX_DRIFT_DELTA_KEYS = 20` — max keys tracked per facet delta

**Brief integration** (`knowledge/analyst_workbench.py`):
- `build_sprint_brief()` — pokud `drift_reasons` je přítomen, přidává `Drift signals: <reason1>, <reason2>, <reason3>` do key_findings (první 3 důvody, concise)
- Backwards-compatible: staré memory bez `drift_reasons` fallback na `drift_ratio` — žádné schema změny

**Definice dokončení**: ✓ pytest tests/probe_f206h/ (16/16 pass), pytest tests/probe_f204d/ tests/probe_f205j/ (55/55 pass), smoke_runner OK

## F203C: Kill Chain Tagger — ATT&CK Mapping (2026-04-25)

**Phase 3 seam — OSINT finding to threat-intel mapping.**

**Role**: Maps raw OSINT findings to MITRE ATT&CK tactics and techniques, enabling threat-intel heat maps instead of raw IOC dumps.

**Dependencies**: F202A (evidence envelope). Benefits from F202C (exposure correlator) and F202D (leak sentinel).

**Module**: `intelligence/kill_chain_tagger.py`

**ATT&CK Coverage**:
| Tactic | Techniques | Phase |
|--------|-----------|-------|
| Reconnaissance (TA0043) | T1590–T1598 | reconnaissance |
| Resource Development (TA0042) | T1583–T1588 | resource_development |

**Pattern types**: ~150 regex/lookup patterns across T1590–T1598 (recon) and T1583–T1588 (resource dev). Techniques covered: DNS records, WHOIS, certificate transparency, passive DNS, subdomains, SSL/TLS, CVE/vulnerability scanning, leaked credentials, phishing kits, infrastructure acquisition, malware tools, VPN services, DNS server, web services, supply chain.

**Bounds**:
- `MAX_TAGS_PER_FINDING=5` — cap per finding
- `MAX_TAGGED_FINDINGS=1000` — cap per sprint
- No model load, no network — deterministic

**Sprint Scheduler Hook** (`runtime/sprint_scheduler.py`):
- `_run_kill_chain_tagging_sidecar(findings, store, query)` — async sidecar after findings are stored
- `kill_chain_tags_produced` counter in `SprintSchedulerResult`
- Wired after `_run_sprint_diff_sidecar()` in the sidecar chain

**Dashboard** (`monitoring/sprint_dashboard.py`):
- Row 8: `kill-chain: N` when `kill_chain_tags_produced > 0`

**Markdown Rendering** (`export/sprint_markdown_reporter.py`):
- `_render_kill_chain_section()` — grouped by tactic, shows technique counts and average confidence
- Scorecard key: `kill_chain_findings`

**Guardrails**:
- Deterministic: pattern matching only, no model inference, no network
- Fail-soft throughout: all errors caught, never crash sprint
- Frozen dataclass for KillChainTag — immutable
- No live_feed_pipeline tuple expansion

## F203E: CTI STIX 2.1 Export (2026-04-26)

**Phase 3 seam — threat-intel bundle export from findings and sidecar data.**

**Role**: Upgrades the diagnostic STIX exporter (Sprint 8BJ) to a real CTI exporter producing STIX 2.1 indicator, identity, observed-data, relationship, note, and report objects from findings, identity candidates, attribution scores, kill-chain tags, and evidence chains.

**Dependencies**: F202A (evidence envelope), F202B (identity stitching), F202C (exposure correlator), F202D (leak sentinel), F203B (attribution scoring), F203C (kill-chain tagging), F203D (evidence chains).

**Module**: `export/stix_exporter.py`

**Public API**:
```python
def render_cti_stix_bundle(
    findings: list[CanonicalFinding | dict],
    identity_candidates: list[dict] | None = None,
    attribution_scores: dict | None = None,
    killchain_tags: dict | None = None,
    evidence_chains: list[dict] | None = None,
    max_objects: int = 500,
) -> dict[str, Any]
```

**STIX object mapping**:
| Input | STIX Object |
|-------|-------------|
| Finding (ip/domain/url/hash) | `indicator` with STIX pattern |
| Finding (non-pattern IOC) | `observed-data` |
| IdentityCandidate | `identity` |
| AttributionScore | `note` (explainable confidence) |
| KillChainTag | `labels` on indicator + `note` per finding |
| EvidenceChain | `observed-data` + `relationship` |
| All CTI | `report` wrapping all objects |

**STIX objects produced**: indicator, identity, observed-data, relationship, note, report.

**Bounds**:
- `MAX_STIX_OBJECTS=500` — streaming-ish object construction
- Deterministic UUID5 from stable namespace+content (same content → same ID)
- No fake IOC objects when findings list is empty
- No network, no model

**Guardrails**:
- Empty findings → no indicator objects (only Ghost Prime identity + report)
- JSON only (no binary)
- No network access, no MLX/model load

**Tests**: `tests/probe_f203e/test_stix_cti_exporter.py` — 41 probe tests (F203E-1 through F203E-15).

**Integration**: `render_cti_stix_bundle_to_path()` available for optional STIX artifact export in sprint_exporter.

## F204F: Production CTI Export Wiring (2026-04-26)

**Phase 3 seam — production CTI STIX 2.1 bundle wired into sprint_scheduler._run_export().**

**Role**: Wires CTI STIX export as a first-class export alongside diagnostic STIX. Produces real CTI STIX 2.1 bundle from CanonicalFindings, identity candidates, attribution scores, kill-chain tags, and evidence chains.

**Dependencies**: F203E (CTI STIX 2.1 bundle renderer), F202A (evidence envelope), F202B (identity stitching), F202C (exposure correlator), F202D (leak sentinel), F203B (attribution scoring), F203C (kill-chain tagging), F203D (evidence chains).

**Module**: `export/stix_exporter.py`, `runtime/sprint_scheduler.py`

**Public API**:
```python
@dataclass(frozen=True)
class CTIExportInputs:
    findings: tuple[Any, ...]
    identity_candidates: tuple[dict[str, Any], ...]
    attribution_scores: dict[str, Any]
    killchain_tags: dict[str, Any]
    evidence_chains: tuple[dict[str, Any], ...]
    sprint_id: str

async def collect_cti_export_inputs(
    report: dict[str, Any],
    store: Any,
) -> CTIExportInputs
```

**Bounds**:
- `MAX_STIX_OBJECTS=500` — RAM guard
- `MAX_EXPORT_FINDINGS=300` — DuckDB query limit
- `MAX_EXPORT_CHAINS=20` — evidence chains cap
- `MAX_EXPORT_BYTES=5_000_000` — serialization size

**GHOST_INVARIANTS**:
- `asyncio.gather(..., return_exceptions=True)` in `collect_cti_export_inputs`
- `_check_gathered()` after gather
- `asyncio.CancelledError` re-raise in `_run_cti_export`
- Large serialization (>1000 objects) via `run_in_executor`
- Fail-soft: CTI export error → `EXPORT_ERROR:cti_stix:{exc}` in `export_paths`, never raised
- Canonical write path unchanged; export reads only

**Export flow**:
```
core/__main__.py run_sprint()
  → SprintScheduler.run()
  → teardown: _run_export()  [diagnostic STIX + CTI STIX]
  → export_paths: ["*.md", "*.jsonld", "*.stix.json", "ghost_cti_*.stix.json"]
```

**Tests**: `tests/probe_f204f/test_production_cti_export.py` — 21 probe tests.

## F205 Baseline — Sprint F204A-I Verification (2026-04-27)

**Scope**: Restore green verification baseline for F204A–I lanes; no new intelligence capabilities.

### Changes
- **F204A** (`test_sidecar_bus_all_sources.py`): Updated `DEFAULT_SIDECAR_RUNNERS` count 9→12; added `passive_fingerprint`, `rir_correlator`, `social_identity_surface` to expected names; updated `_is_heavy_blocked` assertions to use `tuple[bool, str]` unpacking; switched mocks from `sample_uma_status` to `sidecar_admission()`.
- **F205B** (`sidecar_bus.py`): Added `SIDECAR_STAGES` constant defining 3-stage ordering; refactored `run_all_sidecars()` to execute stages sequentially with `gather(return_exceptions=True)` per stage and `_check_gathered()` between stages; `CancelledError` re-raised; fail-soft between stages.
- **F205F** (`sidecar_dispatcher.py`): Refactor only — extracted sidecar dispatch bookkeeping (batch construction, empty guard, skipped tracking, CancelledError propagation, fail-soft) from `SprintScheduler._dispatch_accepted_findings_sidecars()` into `SidecarDispatcher` class; `SprintScheduler` now delegates to `dispatcher.dispatch()`; `SidecarDispatcher` writes skipped sidecars to `result_sink.sidecars_skipped`; updated F205C tests to use dispatcher wiring.
- **probe_8ve** (`circuit_breaker.py`): Added `resilient_fetch()` and `get_transport_for_domain()` as TEST-SEAM ONLY shims. These are NOT wired into production fetch path (per SF-6 audit gate).

### GHOST_INVARIANTS enforced
- `asyncio.gather(..., return_exceptions=True)` + `_check_gathered()` in `run_all_sidecars`
- `asyncio.CancelledError` re-raised, never swallowed
- Fail-soft: sidecar error captured in `SidecarRunResult.skipped_reason`, never propagated
- RAM guard: `governor.sidecar_admission()` blocks heavy sidecars at critical/emergency
- Canonical write path: `async_ingest_findings_batch()` only

## F205B: Explicit Sidecar Ordering Guarantee (2026-04-27)

**Added**: Explicit staged ordering guarantee in `FindingSidecarBus.run_all_sidecars()`.

**SIDECAR_STAGES constant** (`runtime/sidecar_bus.py`):
```python
SIDECAR_STAGES: tuple[tuple[str, ...], ...] = (
    # Stage 1: light extraction — passive signal collection
    ("leak_sentinel", "passive_fingerprint", "evidence_triage", "temporal_archaeology"),
    # Stage 2: correlation — combines signals into exposure/identity/attribution findings
    (
        "exposure_correlator",
        "identity_stitching",
        "sprint_diff",
        "rir_correlator",
        "social_identity_surface",
        "wayback_diff",
    ),
    # Stage 3: derived — kill-chain tagging and embedding (requires correlated signals)
    ("kill_chain_tagging", "embedding"),
)
```

**Stage semantics**:
- **Stage 1 (light extraction)**: Passive signal collection — no dependencies on other sidecars.
- **Stage 2 (correlation)**: Combines signals produced by stage 1 into exposure/identity/attribution findings.
- **Stage 3 (derived)**: Kill-chain tagging and embedding — requires correlated signals from stage 2.

**Execution model** (`run_all_sidecars`):
1. Stages execute sequentially (stage 1 → stage 2 → stage 3)
2. Within each stage, runners execute concurrently via `asyncio.gather(return_exceptions=True)`
3. `_check_gathered()` called after each stage's gather
4. Stage N failure does not stop stage N+1 (fail-soft between stages)
5. `asyncio.CancelledError` re-raised if task is cancelled externally

**Tests**: `tests/probe_f205b/test_sidecar_ordering.py` — 12 probe tests covering stage order, concurrency, failure isolation, and CancelledError handling.

## F205F: Sidecar Dispatcher Extraction (2026-04-27)

**Refactor only**: Extracted sidecar dispatch bookkeeping from `SprintScheduler._dispatch_accepted_findings_sidecars()` into `SidecarDispatcher` class in `runtime/sidecar_dispatcher.py`.

**SidecarDispatcher responsibilities** (what moved out of scheduler):
- SidecarBatch construction for the bus
- Empty findings / None store early return
- Skipped heavy sidecar tracking (UMA / high_water / rss_exceeds)
- CancelledError propagation
- Fail-soft exception handling

**What stays in SprintScheduler**:
- Owns `_sidecar_bus` (FindingSidecarBus instance)
- Owns `_sidecar_dispatcher` (SidecarDispatcher wiring)
- Calls `dispatcher.dispatch()` as single entry point
- Teardown reads `result_sink.sidecars_skipped` (written by dispatcher)

**SidecarBus responsibilities** (unchanged — stays in `runtime/sidecar_bus.py`):
- Staged runner execution via `asyncio.gather(return_exceptions=True)`
- `_check_gathered()` after each stage
- RAM guard via `governor.sidecar_admission()`
- All individual sidecar runner implementations

**DispatchOutcome dataclass** (`runtime/sidecar_dispatcher.py`):
```python
@dataclass(frozen=True)
class DispatchOutcome:
    sprint_id: str
    source_branch: str
    sidecars_skipped: tuple[str, ...]
```

**SidecarDispatcher API** (`runtime/sidecar_dispatcher.py`):
```python
class SidecarDispatcher:
    def __init__(self, bus, governor=None, result_sink=None): ...
    async def dispatch(source_branch, findings, store, query, sprint_id) -> DispatchOutcome: ...
    def reset() -> None: ...
```

**Tests**: `tests/probe_f205f/test_sidecar_dispatcher.py` — 14 probe tests covering empty batch, branch parity, CancelledError re-raise, fail-soft, skipped tracking, result_sink write, and reset.

## F206C: Lifecycle Runner Extraction (2026-04-27)

**Refactor only**: Extracted lifecycle orchestration glue from `SprintScheduler.run()` into `SprintLifecycleRunner` class in `runtime/sprint_lifecycle_runner.py`.

**SprintLifecycleRunner responsibilities** (what moved out of scheduler):
- LifecycleAdapter creation and lifecycle start
- WARMUP→ACTIVE transition (`ensure_active`)
- Periodic `tick()` call
- Wind-down guard (`windup_guard`)
- Post-sleep windup gate (`post_sleep_gate`)
- Sleep with lifecycle tick (`sleep_or_abort`)
- Final phase teardown transitions (`teardown`)

**What stays in SprintScheduler** (canonical owner):
- Branch execution (`_run_one_cycle`)
- Sidecar dispatch
- Advisory evaluation
- Export execution
- Dedup/forensics flush (called by runner before windup break)
- All result bookkeeping
- `SprintScheduler._lc_adapter` still stored for backward compatibility
- `SprintScheduler._final_phase()` kept as fallback for direct test calls

**SprintLifecycleRunner API** (`runtime/sprint_lifecycle_runner.py`):
```python
class SprintLifecycleRunner:
    def __init__(self, lifecycle, adapter): ...
    def setup() -> None: ...
    def tick(now_monotonic=None) -> SprintPhase: ...
    def ensure_active(now_monotonic=None) -> None: ...
    def windup_guard(now_monotonic=None) -> bool: ...
    def post_sleep_gate(now_monotonic=None) -> bool: ...
    async def sleep_or_abort(seconds) -> None: ...
    def teardown() -> None: ...
    @property def abort_requested() -> bool: ...
    @property def abort_reason() -> str: ...
    @property def is_terminal() -> bool: ...
    @property def current_phase() -> str: ...
    @property def wall_clock_start() -> float | None: ...
```

**No new behavior**: Mechanical extraction only. Scheduler remains truth owner for branches, sidecars, advisory, export.

**Tests**: `tests/probe_f206c/` — 14 probe tests covering phase trace equivalence, windup guard, abort, teardown transitions, and current_phase property.

## F206A: Reproducible Baseline Runner + Test Taxonomy (2026-04-27)

**Scope**: Create `run_baseline.py` CLI and `tests/probe_f206a/` lane that establishes reproducible green baseline for F204/F205 probe lanes. Separates green baseline from historical test debt — known failures are reported, never silently hidden.

### Files added
- `run_baseline.py` — CLI baseline runner
  - `python run_baseline.py --profile f205-green --json PATH`
  - Profiles: `f205-green` (F204 + F205 probe lanes, smoke, inventory)
  - `--collect-only` for inventory without test execution
  - JSON schema: `profile, commands, passed, failed, known_failures, duration_s, test_inventory`
- `tests/probe_f206a/` — probe lane for baseline runner
- `pytest.ini` — minimal markers registration (slow, stress, timeout, unit, integration, smoke, hermetic, probe)

### Probe lanes in green baseline (f205-green profile)
F204 lanes: probe_f204a through probe_f204j (10 lanes)
F205 lanes: probe_f205b through probe_f205j (9 lanes)
Smoke: smoke_runner.py --smoke

### Known failures (reported, not silenced)
Known failure patterns from pre-F205 historical lanes are listed in `KNOWN_FAILURE_PATTERNS` in `run_baseline.py` and reported in JSON output under `known_failures`. These are NOT hidden — they are explicitly enumerated for traceability.

Historical failures (NOT in green baseline, NOT blocking):
- `tests/probe_2a/test_sprint_2a.py` — stale `autonomous_orchestrator` expectations
- `tests/probe_4a/test_lifecycle_4a.py` — stale symbol expectations
- `tests/probe_1b/test_uma_budget.py`, `tests/probe_6b/test_uma_budget_thresholds.py` — UMA snapshot shape drift
- `tests/probe_4b/test_fetch_4b.py` — fetch coordinator API drift
- `tests/probe_6a/test_async_hygiene.py`, `tests/probe_7a/test_sprint_7a.py` — missing `GHOST_INVARIANTS.md`
- `tests/probe_6b/test_mlx_cache_limits.py`, `tests/probe_7b/test_mlx_init.py` — `_MLX_CACHE_LIMIT` export drift

### GHOST_INVARIANTS reminder
- `asyncio.gather(..., return_exceptions=True)` + `_check_gathered()` in all gather calls
- `asyncio.CancelledError` re-raised, never swallowed
- No blocking calls in event loop (CPU/IO via `run_in_executor`)
- Canonical write path: `async_ingest_findings_batch()` only
- RAM guard: skip heavy ops when RSS > high_water

**Tests**: `tests/probe_f206a/test_baseline_runner.py` — 10 probe tests (F206A-1 through F206A-10):
- F206A-1: valid JSON output
- F206A-2: required keys present
- F206A-3: test_inventory.collected_tests is int >= 0
- F206A-4: --collect-only flag works
- F206A-5: known_failures is list
- F206A-6: duration_s is float >= 0
- F206A-7: commands non-empty
- F206A-8: profile matches argument
- F206A-9: failed is non-negative int
- F206A-10: smoke step included in commands

**Definition of Done:**
- `pytest tests/probe_f206a/ -q` (10/10 pass)
- `python smoke_runner.py --smoke` (smoke OK)
- `python run_baseline.py --profile f205-green --json /tmp/hledac_baseline.json` (JSON valid)

## F206B: Shadow Diagnostics Verdicts + Loose Test Migration (2026-04-27)

**Scope**: Audit shadow system modules, classify as ACTIVE/DORMANT/ORPHAN, move loose tests, add verdict tests.

### Verdict classifications

| Module | Verdict | Rationale |
|--------|---------|-----------|
| `runtime/shadow_inputs.py` | ACTIVE (diagnostic) | Pure shadow inputs collector — čte facts z canonical modulů, žádné side effects |
| `runtime/shadow_parity.py` | ACTIVE (diagnostic) | Shadow parity runner — pure function, DIAGNOSTICKÝ artifact, ne truth store |
| `runtime/shadow_pre_decision.py` | ACTIVE (diagnostic) | Read-only consumer layer — skládá PreDecisionSummary, NIKDY nevolá canonical write path |
| `runtime/windup_engine.py` | DORMANT (donor) | Definovaná ale NIKDY nevolaná v produkci — donor pro budoucí použití |

### Key constraints enforced

- **DIAGNOSTIC ONLY**: Shadow output (PreDecisionSummary, ParityArtifact) je read-only diagnostic artifact, NOT a truth store
- **NO canonical write path**: shadow modules NESMÍ volat `async_ingest_findings_batch()` ani tool execution
- **NO execution authority**: shadow čte pouze — sprint scheduler retainuje veškerou decision authority
- **Clean separation**: `consume_shadow_pre_decision()` → `evaluate_advisory_gate()` → `_build_shadow_readiness_preview()` jsou všechny read-only

### Files changed

- `runtime/shadow_inputs.py` — added **VERDICT: ACTIVE (diagnostic only)** block
- `runtime/shadow_parity.py` — added **VERDICT: ACTIVE (diagnostic only)** block
- `runtime/shadow_pre_decision.py` — added **VERDICT: ACTIVE (diagnostic only)** block
- `runtime/windup_engine.py` — clarified **VERDICT: DORMANT (donor/alternate)** block
- `tests/probe_8vk_shadow_parity.py` → `tests/probe_8vk/test_shadow_parity.py` (moved, no semantic change)
- `tests/probe_f206b/test_shadow_verdicts.py` (new) — 15 probe tests

### Tests added

`tests/probe_f206b/test_shadow_verdicts.py` — 15 probe tests:
- `TestShadowDiagnosticVerdict` (4 tests): verify verdict blocks in all shadow modules
- `TestShadowNoCanonicalWritePath` (3 tests): AST scan — žádné volání `async_ingest_findings_batch`
- `TestShadowNoToolExecution` (3 tests): AST scan — žádné volání tool execution
- `TestShadowExportReadOnlySeam` (3 tests): `consume_shadow_pre_decision()` a `_build_shadow_readiness_preview()` jsou read-only
- `TestShadowModuleBoundaries` (2 tests): pure functions, idempotent parity

**Tests**: `pytest tests/probe_f206b/ tests/probe_8vk/ tests/probe_8vl_shadow_pre_decision/ tests/probe_8vm/ -q`

**Definition of Done:**
- `pytest tests/probe_f206b/ -q` (15/15 pass)
- `pytest tests/probe_8vk/ tests/probe_8vl_shadow_pre_decision/ tests/probe_8vm/ -q` (existing tests still pass)
- `python smoke_runner.py --smoke` (smoke OK)

## F206D: Advisory Runner Extraction (2026-04-27)

**Refactor only**: Extracted advisory evaluation from `SprintScheduler._run_teardown_advisories()` into `SprintAdvisoryRunner` class in `runtime/sprint_advisory_runner.py`.

**SprintAdvisoryRunner responsibilities** (what moved out of scheduler):
- Sequential advisory step execution: planner → executor → governor → brief
- `AdvisoryRunOutcome` dataclass construction
- Planner step: `PivotPlanner.plan_pivots()` → `_planned_pivots`
- Executor step: `SprintAdvisoryRunner._run_advisory_executor()` → `_executed_advisories`
- Governor step: records skipped sidecars, peak RSS from result
- Brief step: `AnalystWorkbench.build_sprint_brief()` → `_analyst_brief`
- Fail-soft per step; `CancelledError` re-raised

**What stays in SprintScheduler** (canonical owner):
- AdvisoryRunner instantiation and `run_all_advisories()` call in teardown
- `inject_analyst_workbench()` for on-demand workbench creation from `self._duckdb_store`
- `_planned_pivots`, `_analyst_brief`, `_governor_recorded` state access

**SprintAdvisoryRunner API** (`runtime/sprint_advisory_runner.py`):
```python
class SprintAdvisoryRunner:
    def __init__(self, scheduler, duckdb_store, graph_service): ...
    async def run_all_advisories(sprint_id, query) -> AdvisoryRunOutcome: ...

@dataclass(frozen=True)
class AdvisoryRunOutcome:
    planned_pivots: int
    executed_pivots: int
    governor_recorded: bool
    brief_generated: bool
    error: str | None
```

**Advisory step order** (`run_all_advisories`):
1. `_run_pivot_planner_advisory()` → `planned_pivots`
2. `_run_advisory_executor()` → `executed_pivots`
3. `_run_governor_advisory()` → `governor_recorded`
4. `_run_analyst_brief_advisory()` → `brief_generated`

Each step is fail-soft; partial outcomes are returned. `asyncio.CancelledError` propagates.

**Tests**: `tests/probe_f206d/test_advisory_runner.py` — tests for AdvisoryRunOutcome frozen dataclass, runner construction, sequential step execution, fail-soft per step, CancelledError propagation, governor RSS tracking, and brief generation with query as target_id.

## F206E: Windup Scorecard Reporting (2026-04-27)

**Scope**: Active diagnostic report includes bounded windup scorecard fields extracted read-only from dormant `windup_engine.py` donor — WITHOUT activating the dormant `run_windup()` path.

**Module**: `runtime/sprint_scheduler.py` — `_get_windup_scorecard()` method

**WindupScorecard fields** (bounded by `MAX_WINDUP_SCORECARD_KEYS=32`):
| Field | Source |
|-------|--------|
| `cb_open_domains` | `get_all_breaker_states()` — domains in open/half_open |
| `cb_tracked_count` | Total circuits tracked |
| `phase_durations.warmup_s` | `result.pre_loop_elapsed_s` |
| `phase_durations.active_s` | `entered_active_at_monotonic` → `first_cycle_started_at_monotonic` |
| `graph_nodes`, `graph_edges`, `graph_pgq_available` | `_get_graph_signal()` |
| `peak_rss_mb` | `result.peak_rss_gib * 1024` |
| `accepted_findings` | `result.accepted_findings` |
| `sidecar_findings` | Aggregated from `identity/exposure/timeline/leak/evidence_triage/forensics/multimodal` counts |
| `branch_timeouts` | `result.branch_timeout_count` when > 0 |
| `budget_violations` | `result.budget_violations` when > 0 |

**Key constraints**:
- NO model load or GNN imports in `_get_windup_scorecard()`
- NO call to `run_windup()` — dormant path stays dormant
- Fail-soft: returns `{}` when all data sources unavailable
- Priority key pruning when `len(scorecard) > MAX_WINDUP_SCORECARD_KEYS`

**Tests**: `tests/probe_f206e/test_windup_scorecard_reporting.py` — 14 probe tests (F206E-1 through F206E-14) covering fail-soft, circuit breaker state, phase durations, graph stats, memory, findings, sidecar aggregation, branch timeouts, budget violations, no model load, and dormant path enforcement.

## F206F: DHT/IPFS Promotion Gate (2026-04-27)

**Scope**: Explicit promotion gate status for DHT and IPFS modules — both are bounded/experimental, NOT production-ready for full autonomous operation.

**DHT promotion status** (`dht/kademlia_node.py`):
- `DHT_PROMOTION_STATUS = "simulated_no_persist"` — DHT simulation active, no real persistence
- `is_dht_production_ready()` → `False`

**IPFS promotion status** (`network/ipfs_client.py`):
- `IPFS_PROMOTION_STATUS = "bounded_gateway_fetch"` — bounded gateway fetch only
- `fetch_ipfs(timeout=30, ...)` with default 30s timeout
- `MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024` (10MB cap)
- `fetch_ipfs` fails soft — returns `None` on error
- `ipfs_content_to_finding_dict` uses `source_type="ipfs_fetch"`
- `deep_probe.scan_ipfs` uses `source_type="deep_probe_ipfs"`

**Circuit breaker integration**:
- Circuit breaker hook in IPFS is optional and fail-open
- IPFS errors do NOT trip the circuit breaker

**Tests**: `tests/probe_f206f/test_dht_ipfs_promotion_gate.py` — 10 probe tests (F206F-1 through F206F-10) covering DHT/IPFS promotion status, fetch timeout, size cap, fail-soft behavior, source_type tagging, and circuit breaker fail-open.

## F206G: Graph Analytics Activation (2026-04-27)

**Scope**: Bounded graph analytics signal activated for analyst brief and sprint report — WITHOUT creating a new graph authority.

**Module**: `knowledge/graph_service.py` — `graph_analytics_summary()` function

**`graph_analytics_summary()` API**:
```python
def graph_analytics_summary(top_k: int = 10) -> dict:
    # Returns:
    #   top_central_entities: list of {value, ioc_type, degree}
    #   community_count: int
    #   analytics_available: bool
    #   skipped_reason: str | None
```

**Bounds**:
- `MAX_GRAPH_ANALYTICS_TOP_K = 10` — top_k cap
- `MAX_GRAPH_ANALYTICS_NODES = 500` — node query LIMIT
- Read-only: no `checkpoint()`, no INSERT/UPDATE/DELETE

**`build_sprint_brief` integration**:
- Calls `graph_analytics_summary()` for graph signal
- Includes at most 2 graph analytics findings in brief key_findings
- Fail-soft: returns empty signal when graph unavailable
- Community count used as second finding when only 1 top entity

**Key constraint**: `graph_analytics_summary()` is read-only — no persistent writes to the graph store.

**Tests**: `tests/probe_f206g/test_graph_analytics_activation.py` — 9 probe tests (F206G-1 through F206G-9) covering required structure, empty when unavailable, fail-soft, top_k bounded, MAX_GRAPH_ANALYTICS_NODES respected, no persistent writes, brief integration (up to 2 findings, excludes when unavailable, fail-soft).

## F206H: Target Drift Intelligence (2026-04-27)

**Scope**: Explainable target drift intelligence — confidence_drift now includes entity/exposure/pivot delta keys and drift_reasons list, not just drift_ratio.

**Module**: `knowledge/target_memory.py` — `TargetMemoryService.merge_update()`

**New `confidence_drift` keys** (present after first merge):
| Key | Description |
|-----|-------------|
| `entity_delta` | `{added, removed, stable, total_prev, total_curr, top_added, top_removed}` |
| `exposure_delta` | Same structure for exposure facets |
| `pivot_delta` | Same structure for pivot facets |
| `drift_reasons` | List[str] — bounded explanations for drift |

**`_compute_facet_delta` structure**:
```python
{
    "added": int,        # new types not in prior memory
    "removed": int,      # types dropped since prior memory
    "stable": int,      # types present in both
    "total_prev": int,   # total types in prior memory
    "total_curr": int,  # total types in current memory
    "top_added": list,   # new types sorted by score
    "top_removed": list, # dropped types sorted by score
}
```

**Bounds**:
- `MAX_DRIFT_REASONS = 8` — drift_reasons list cap
- `MAX_DRIFT_DELTA_KEYS = 20` — entity_delta.total_curr cap
- Legacy fallback: old memory without delta keys uses `drift_ratio` as-is

**`build_sprint_brief` integration**:
- Drift explanation from `drift_reasons` appears in brief key_findings
- Legacy memory without delta keys falls back to `drift_ratio` headline

**Tests**: `tests/probe_f206h/test_target_drift_intelligence.py` — 13 probe tests (F206H-1 through F206H-13) covering confidence_drift keys, facet_delta structure, added/removed entity counting, bounds enforcement, first-sprint legacy keys, brief drift explanation, and backwards compatibility.

## F206I: Baseline Regression Extension (2026-04-27)

**Scope**: Extend the reproducible baseline runner to include F206A–H probe lanes in the regression profile and add known-failure cluster tracking.

**Files changed**:
- `run_baseline.py` — added `f206-regression` profile including F206A–F206I probe lanes
- `KNOWN_FAILURE_PATTERNS` updated to include F204/F205/F206 specific failure markers

**Probe lanes in f206-regression profile**:
F204 lanes: probe_f204a through probe_f204j (10 lanes)
F205 lanes: probe_f205b through probe_f205j (9 lanes)
F206 lanes: probe_f206a through probe_f206i (9 lanes)

**Known failure cluster report**:
- Pre-F204 historical failures reported separately (not silenced)
- Smoke failures (AdaptiveSemaphore) reported under known_failures
- Benchmark matrix reports per-lane pass/fail with failure clustering

**Tests**: `tests/probe_f206i/test_baseline_runner.py` — probes for the f206-regression profile, including inventory collection, JSON schema validation, known-failure reporting, and per-lane pass/fail aggregation.

## F206J: Architecture Seal (2026-04-27)

**Scope**: Seal F206 series — document active/dormant/orphan verdicts, scheduler decomposition, benchmark matrix, and known failure clusters. No new functionality.

### Active / Dormant / Orphan verdicts after F206

| Module | Verdict | Rationale |
|--------|---------|-----------|
| `runtime/shadow_inputs.py` | ACTIVE (diagnostic) | Pure shadow inputs collector — reads facts from canonical modules, no side effects |
| `runtime/shadow_parity.py` | ACTIVE (diagnostic) | Shadow parity runner — pure function, DIAGNOSTIC artifact, NOT truth store |
| `runtime/shadow_pre_decision.py` | ACTIVE (diagnostic) | Read-only consumer — builds PreDecisionSummary, NEVER calls canonical write path |
| `runtime/windup_engine.py` | DORMANT (donor) | Defined but NEVER called in production — donor for future use |
| `runtime/sprint_lifecycle_runner.py` | ACTIVE (canonical) | Extracted lifecycle orchestration — WARMUP→ACTIVE→WINDUP→TEARDOWN |
| `runtime/sprint_advisory_runner.py` | ACTIVE (canonical) | Extracted advisory runner — planner→executor→governor→brief sequence |
| `runtime/sidecar_dispatcher.py` | ACTIVE (canonical) | Extracted sidecar dispatch — batch construction, skipped tracking |
| `runtime/sidecar_bus.py` | ACTIVE (canonical) | Staged runner execution — gather+_check_gathered per stage |
| `dht/kademlia_node.py` | DORMANT (experimental) | `DHT_PROMOTION_STATUS = "simulated_no_persist"` — not production |
| `network/ipfs_client.py` | DORMANT (experimental) | `IPFS_PROMOTION_STATUS = "bounded_gateway_fetch"` — bounded gateway only |
| `knowledge/graph_service.py` | ACTIVE (canonical) | Graph analytics for analyst brief — DuckPGQ-backed, read-only |
| `knowledge/target_memory.py` | ACTIVE (canonical) | Target memory with drift intelligence — delta keys + drift_reasons |

### Scheduler decomposition

The canonical sprint scheduler is decomposed into three extracted runners plus the scheduler itself:

```
SprintScheduler.run()
├── SprintLifecycleRunner          # lifecycle orchestration
│   ├── setup() → WARMUP
│   ├── ensure_active() → ACTIVE
│   ├── tick() periodic
│   ├── sleep_or_abort()
│   ├── windup_guard / post_sleep_gate
│   └── teardown() → WINDUP→TEARDOWN
├── _run_one_cycle()               # canonical: branch execution (stays in scheduler)
├── SidecarDispatcher.dispatch()    # sidecar batch dispatch
│   └── FindingSidecarBus.run_all_sidecars()
│       ├── Stage 1 (light): leak_sentinel, passive_fingerprint, evidence_triage, temporal_archaeology
│       ├── Stage 2 (correlation): exposure_correlator, identity_stitching, sprint_diff, rir_correlator, social_identity_surface, wayback_diff
│       └── Stage 3 (derived): kill_chain_tagging, embedding
└── SprintAdvisoryRunner.run_all_advisories()
    ├── _run_pivot_planner_advisory()
    ├── _run_advisory_executor()
    ├── _run_governor_advisory()
    └── _run_analyst_brief_advisory()
```

### Benchmark matrix

| Metric | F204 Baseline | F205 Extension | F206 Seal |
|--------|--------------|---------------|-----------|
| Probe lanes | F204a–j (10) | F205b–j (9) | F206a–i (9) |
| Total probes | ~200 | ~180 | ~150 |
| Canonical write path | async_ingest_findings_batch | async_ingest_findings_batch | async_ingest_findings_batch |
| Lifecycle | SprintLifecycleManager | SprintLifecycleManager | SprintLifecycleRunner extracted |
| Sidecar bus | FindingSidecarBus staged | 3-stage ordering guaranteed | SidecarDispatcher extracted |
| Advisory | inline in teardown | inline in teardown | SprintAdvisoryRunner extracted |
| Shadow system | ACTIVE diagnostic | ACTIVE diagnostic | ACTIVE diagnostic |
| Graph analytics | read via DuckPGQ | read via DuckPGQ | graph_analytics_summary activated |
| Target drift | drift_ratio only | drift_ratio only | delta keys + drift_reasons |
| DHT/IPFS | — | — | DORMANT experimental gates |
| Windup scorecard | — | — | _get_windup_scorecard (read-only) |

### Known failure clusters

| Cluster | Files | Status |
|---------|-------|--------|
| AdaptiveSemaphore smoke | smoke_runner.py | Pre-existing — AdaptiveSemaphore.__init__ no `initial_value` |
| Historical probe lanes | probe_2a, probe_4a, probe_6a–7a | Stale symbol/API expectations |
| UMA snapshot shape | probe_1b, probe_6b | Shape drift since F195 |
| Fetch coordinator API | probe_4b | API drift |

### GHOST_INVARIANTS enforced across F206

- `asyncio.gather(..., return_exceptions=True)` + `_check_gathered()` in all gather calls
- `asyncio.CancelledError` re-raised, never swallowed
- No blocking calls in event loop (CPU/IO via `run_in_executor`)
- Canonical write path: `async_ingest_findings_batch()` only
- RAM guard: skip heavy ops when RSS > high_water
- Fail-soft: sidecar/advisory error never crashes sprint

**Tests**: `tests/probe_f206j/test_f206_architecture_seal.py` — architecture seal probe tests

**Definition of Done:**
- `pytest tests/probe_f206j/ -q`
- `python run_baseline.py --profile f206-regression --json /tmp/f206_regression.json`
- `python smoke_runner.py --smoke`
- `python benchmarks/e2e_canonical_benchmark.py --hermetic --runs 3`

## Architectural verdict

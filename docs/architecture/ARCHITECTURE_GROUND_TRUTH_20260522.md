# Architecture Ground Truth — 20260522

> Built from live source code analysis — not from `.md` docs.
> SOURCE: `filename:line` references below.

---

## Entry Point

### Canonical Path
```
python -m hledac.universal --sprint "LockBit ransomware" --duration 1800
```

**Root `__main__.py`** (627KB, 2009 lines):
- `async def main()` — entry point at line ~262
- Top-level imports: `asyncio`, `logging`, `pathlib`, `signal`, `typing`, `msgspec`
- 3 signal handlers, 5 BootGuard checks

**Argument flags** (`__main__.py:add_argument`):
```
--sprint QUERY          Run sprint with given query (positional)
--duration SECS         Sprint duration in seconds (default: 1800 = 30min)
--export-dir PATH       Export directory
--aggressive            Sprint F195B: Enable aggressive mode with 8s branch budgets
--deep-probe            Run deep probe research post-sprint
--ui                    Enable terminal dashboard during sprint
--acquisition-profile   Choices: default, nonfeed_diagnostic, deep_osint_m1
```

**Delegation chain**:
```
main() 
  → core.__main__.run_sprint()  [canonical — SOLE sprint owner]
  → SprintScheduler.run()
```

### `core/__main__.py` (122KB, 3312 lines)
- Role table (line ~522): `run_sprint()` = canonical/sole owner
- Functions: `_make_sprint_id`, `_is_meaningful_run`, `_runtime_truth`, `run_pre_sprint_checks`, `write_sprint_delta`, `run_sprint`, `run_ct_pivot`, `run_semantic_pivot`, `main`
- `main()` at line ~1087: parses args, calls `run_sprint()`

---

## Sprint Lifecycle

### Canonical Owner: `SprintScheduler.run()` (runtime/sprint_scheduler.py)

**Method**: `async def run(self, query: str, duration: float = 1800.0, ...)` at line 2433

**Lifecycle phases**:

| Phase | Method | Purpose |
|-------|--------|---------|
| Pre-flight | `run_pre_sprint_checks()` | Boot guard, UMA sampling, GC config |
| Feed branch | `_run_feed_branch()` | RSS/CT log discovery |
| Public branch | `_run_public_branch()` | DuckDuckGo, searxng, shodan |
| CT branch | `_run_ct_branch()` | Cert transparency, passive DNS |
| Nonfeed predispatch | `_run_feed_dominance_nonfeed_rescue_window()` | Feed dominance detection |
| Nonfeed prelude | `_run_mandatory_acquisition_prelude()` | Build acquisition plan |
| Nonfeed lanes | `_run_lane()` | wayback, pdns, doh, wayback_cdx, etc. |
| Advisory runners | `_run_advisory_runner()` | Pivot, leak sentinel, identity stitch, etc. |
| Windup barrier | `_run_prewindup_barrier()` | Pre-export checks |
| Export | `_run_export()` | Markdown, CTI, file export |
| Teardown | `write_sprint_delta()` | DuckDB write |

**Loop structure**: `while time.monotonic() < hard_deadline` (line 2433+)
- Branch budget: 8s (aggressive) or 25s (normal)
- Cycle metrics collected per `_run_one_cycle()`

### Active Adapters Called From `SprintScheduler`

| Adapter | Source | Purpose |
|---------|--------|---------|
| `duckdb_store` | `knowledge/duckdb_store.py` | Canonical write, shadow analytics |
| `ct_log_client` | `fetching/ct_log_client.py` | CT log discovery |
| `public_fetcher` | `fetching/public_fetcher.py` | HTTP/stealth fetch |
| `policy_manager` | `runtime/sprint_policy_manager.py` | RL policy opt-in |
| `enrichment_services` | `runtime/enrichment_services.py` | Forensics, multimodal |
| `hermes3_engine` | `brain/hermes3_engine.py` | LLM synthesis |
| `graph_service` | `knowledge/graph_service.py` | IOC graph accumulation |

---

## Canonical Data Contracts

### CanonicalFinding (knowledge/duckdb_store.py:229)

```python
class CanonicalFinding(msgspec.Struct, frozen=True, gc=False):
    # Identity
    finding_id: str
    query: str
    source_type: str
    confidence: float
    ts: float

    # Provenance chain (not provenance_json string)
    provenance: tuple[str, ...] = ()

    # Optional — stored in LMDB WAL payload, NOT in DuckDB INSERT
    payload_text: str | None = None
```

**Quality gate (separate class, same file:264)**:
```python
class FindingQualityDecision(msgspec.Struct, frozen=True, gc=False):
    accepted: bool
    reason: str | None
    entropy: float           # bits per character
    normalized_hash: str | None  # BLAKE2b hex, 32 chars
    duplicate: bool
```

**Notes**:
- Fields `ioc_type`, `ioc_value`, `entropy`, `normalized_hash`, `duplicate` belong to `FindingQualityDecision`, not `CanonicalFinding`
- `provenance` is `tuple[str, ...]`, not JSON string

### SprintResult (runtime/sprint_scheduler.py:1346)

**Core cycle metrics**:
- `cycles_started: int`, `cycles_completed: int`, `findings_collected: int`
- `accepted_findings: int`, `rejected_findings: int`, `dedup_hits: int`
- `new_iocs_ingested: int`, `runtime_s: float`, `wall_clock_s: float`

**Timing fields**:
- `synthesis_duration_s: float`, `public_duration_s: float`, `ct_duration_s: float`
- `nonfeed_duration_s: float`, `enrichment_duration_s: float`

**Telemetry** (nonfeed predispatch):
- `nonfeed_predispatch_checked: bool`, `nonfeed_predispatch_ran: bool`
- `nonfeed_predispatch_reason: str | None`, `nonfeed_predispatch_outcomes_count: int`

**Telemetry** (acquisition prelude):
- `acquisition_prelude_checked: bool`, `acquisition_prelude_ran: bool`
- `acquisition_prelude_required_lanes: tuple[str, ...]`
- `acquisition_prelude_terminal_lanes: tuple[str, ...]`
- `acquisition_prelude_missing_lanes: tuple[str, ...]`
- `acquisition_prelude_skipped_lanes: dict[str, str]`
- `acquisition_prelude_errors: dict[str, str]`
- `acquisition_prelude_plan_present: bool`, `acquisition_prelude_plan_built_for_prelude: bool`
- `acquisition_prelude_domain_detected: bool`, `acquisition_prelude_domain_detection_error: str`
- `acquisition_prelude_reason: str`

**Return guard telemetry**:
- `return_guard_block_reason: str`
- `return_guard_attempted_lanes: tuple[str, ...]`
- `return_guard_skipped_lanes: dict[str, str]`
- `return_guard_errors: dict[str, str]`
- `return_guard_blocks_triggered: int`

**Prewindup barrier**:
- `windup_delayed_for_nonfeed: bool`
- `prewindup_barrier_checked: bool`, `prewindup_barrier_required_lanes: tuple[str, ...]`
- `prewindup_barrier_satisfied: bool`, `prewindup_barrier_attempted_lanes: tuple[str, ...]`
- `prewindup_barrier_skipped_lanes: dict[str, str]`, `prewindup_barrier_errors: dict[str, str]`
- `prewindup_barrier_duration_s: float`, `prewindup_barrier_delayed_cycle: bool`

**Branch blockers**:
- `dominant_feed_blocker: str`, `dominant_branch_blocker: str`
- `branch_degradation_summary: str`, `feed_zero_yield_detected: bool`

**Lane outcomes**:
- `acquisition_lane_outcomes: tuple`
- `lane_ct_accepted_findings: int`, `lane_wayback_accepted_findings: int`, `lane_pdns_accepted_findings: int`

**Feed suppression**:
- `feed_suppressed_by_budget: int`, `feed_suppressed_by_nonfeed_budget: int`
- `feed_suppression_count: int`, `feed_suppression_reason: str`

**Dedup preload**:
- `dedup_preload_count: int | None`, `dedup_preload_elapsed_s: float | None`

**Nonfeed budget**:
- `nonfeed_budget_active: bool`
- `nonfeed_budget_expected_lanes: tuple[str, ...]`
- `nonfeed_budget_terminal_lanes: tuple[str, ...]`
- `nonfeed_budget_unresolved_lanes: tuple[str, ...]`

**Hard deadline**:
- `hard_deadline_exceeded_at_cycle: bool`
- `hard_deadline_remaining_s_at_exit: float`

**Acquisition termility**:
- `acquisition_terminality_checked: bool`, `acquisition_terminality_satisfied: bool`
- `acquisition_terminality_missing_lanes: tuple[str, ...]`, `acquisition_terminality_report: str`

---

## Source Type Registry (62 values)

Defined as string literals across codebase (not centralized Literal type):

| Category | Values |
|----------|--------|
| CT/Feed | `ct_log`, `rss_feed`, ` PassiveDNS`, `tot_synthesis` |
| Discovery | `duckduckgo_search`, `bing_search`, `searxng`, `shodan_search`, `google_routine` |
| OSINT | `azure_risky_signins`, `shodan_host_banner`, `cirrus_fleet`, `cloud_hard`, `otx_pulse`, `alienvault_fresh`, `threatfox`, `mis4` |
| Archives | `wayback_archive`, `wayback_cdx`, `wayback_diff`, `archive_today`, `temporal_archaeology` |
| Nonfeed | `nonfeed_candidates`, `acq_candidates`, `nonfeed_predispatch`, `wayback_enumeration`, `pdns_enumeration`, `doh_resolution` |
| Enumeration | `cert_transparency`, `circuits_hint`, `citrix_scan`, `crtsh_adapter`, `circl_pvil`, `asset_exposure` |
| Identity | `social_identity_surface`, `social_graph_enumeration` |
| Sidecars | `identity_stitching`, `leak_sentinel`, `pivot_planner`, `deep_probe` |
| Synthesis | `aggressive_synthesis`, `sprint_diff`, `graph_enrichment` |
| Export | `file_export`, `stix21_export`, `markdown_export` |
| Research | `research_episode`, `hypothesis_feedback` |

---

## Storage Schema — DuckDB (knowledge/duckdb_store.py)

### Tier 1: Sprint Facts (durable)

**shadow_findings** (line ~440):
```sql
id              VARCHAR PRIMARY KEY,
query           VARCHAR,
source_type     VARCHAR,
confidence      DOUBLE,
provenance_json TEXT,
UNIQUE (query, source_type)
```

**shadow_runs** (line ~449):
```sql
run_id      VARCHAR PRIMARY KEY,
started_at  TIMESTAMP,
ended_at    TIMESTAMP,
total_fds   INTEGER,
rss_mb      INTEGER
```

**sprint_delta** (line ~456):
```sql
sprint_id TEXT PRIMARY KEY,
ts DOUBLE NOT NULL,
query TEXT,
duration_s REAL DEFAULT 0,
new_findings INT DEFAULT 0,
dedup_hits INT DEFAULT 0,
ioc_nodes INT DEFAULT 0,
ioc_new_this_sprint INT DEFAULT 0,
uma_peak_gib REAL DEFAULT 0,
synthesis_success BOOL DEFAULT false,
findings_per_minute REAL DEFAULT 0,
top_source_type TEXT,
synthesis_confidence REAL DEFAULT 0
```

**source_hit_log** (line ~471):
```sql
sprint_id TEXT,
ts DOUBLE,
source_type TEXT,
findings_count INT,
ioc_count INT,
hit_rate REAL
```

**sprint_scorecard** (line ~479):
```sql
sprint_id TEXT PRIMARY KEY,
ts DOUBLE NOT NULL,
findings_per_minute REAL,
ioc_density REAL,
semantic_novelty REAL,
source_yield_json TEXT,
phase_timings_json TEXT,
outlines_used BOOL,
accepted_findings INT,
ioc_nodes INT
```

**research_episodes** (line ~491):
```sql
episode_id   TEXT PRIMARY KEY,
sprint_id    TEXT NOT NULL,
query        TEXT NOT NULL,
summary      TEXT,
top_findings JSON,
ioc_clusters JSON,
source_yield JSON,
synthesis_engine TEXT,
duration_s   REAL
```

### Tier 2: Entity Memory

**target_profiles** (line ~504):
```sql
target_id TEXT PRIMARY KEY,
first_seen DOUBLE,
last_seen DOUBLE,
cumulative_finding_count INTEGER,
entity_summary_json TEXT
```

**hypothesis_feedback** (line ~511):
```sql
id TEXT PRIMARY KEY,
target_id TEXT,
pivot_type TEXT,
ioc_type TEXT,
produced_count INTEGER,
accepted_count INTEGER,
signal_value DOUBLE,
ts DOUBLE
```

**target_memory** (line ~521):
```sql
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
```

**global_entities** (line ~2995):
```sql
entity_value TEXT PRIMARY KEY,
entity_type TEXT,
sprint_count INT DEFAULT 0,
last_seen DOUBLE,
confidence_cumulative REAL DEFAULT 0
```

---

## Module Dependency Map

### Top-level imports from root `__main__.py`:

```
asyncio, contextlib, logging, os, pathlib, signal, sys, time, typing,
msgspec, time, pathlib, signal, logging
```

### Key seams (from `core/__main__.py` role table):

| Module | Role | Notes |
|--------|------|-------|
| `SprintScheduler` | canonical sprint owner | `run()` at line 2433 |
| `duckdb_store` | canonical write seam | `async_ingest_findings_batch()` |
| `ct_log_client` | CT discovery | `_run_ct_branch()` |
| `public_fetcher` | stealth HTTP | `curl_cffi` transport |
| `hermes3_engine` | LLM synthesis | brain/ |
| `enrichment_services` | forensics + multimodal | runtime/ |
| `graph_service` | IOC graph | knowledge/ |

---

## Dead Code Candidates

From `__main__.py` role table analysis (line ~130):
- `_run_sprint_mode()`: "alternate/deprecated/unreachable"
- `run_warmup()`: "dormant, only called by dead _run_sprint_mode"

---

## Active Discovery Adapters

Confirmed called from `SprintScheduler.run()` lifecycle (grep-verified):

| Adapter | File | Called via |
|---------|------|-----------|
| `DuckDuckGoAdapter` | discovery/duckduckgo_adapter.py | `_run_public_branch()` |
| `SearxngAdapter` | discovery/searxng_adapter.py | `_run_public_branch()` |
| `ShodanSearchAdapter` | discovery/shodan_adapter.py | `_run_public_branch()` |
| `CtLogClient` | fetching/ct_log_client.py | `_run_ct_branch()` |
| `PassiveDNSAdapter` | intelligence/pdns_adapter.py | `_run_pdns_for_domain()` |
| `DoHProvider` | intelligence/doh_resolver.py | `_run_doh_prelude_lane()` |
| `WaybackCDXAdapter` | intelligence/wayback_cdx.py | `_run_wayback_cdx_deep_sidecar()` |
| `IdentityStitchingAdapter` | intelligence/identity_stitching_canonical.py | `_run_identity_stitching_sidecar()` |
| `LeakSentinelAdapter` | security/leak_sentinel.py | `_run_leak_sentinel_sidecar()` |
| `TemporalArchaeologistAdapter` | intelligence/temporal_archaeologist_adapter.py | `_run_temporal_archaeology_sidecar()` |
| `PivotPlannerAdapter` | intelligence/pivot_planner.py | `_run_pivot_planner_advisory()` |

---

## Key Corrections (Post-Review)

| Issue | Original Claim | Verified Reality |
|-------|----------------|------------------|
| CanonicalFinding fields | Listed 11 fields incl. ioc_type, entropy, normalized_hash | Only 7 fields: finding_id, query, source_type, confidence, ts, provenance, payload_text. Quality fields are in FindingQualityDecision (duckdb_store.py:264) |
| SprintSchedulerResult | Marked as not found | EXISTS at sprint_scheduler.py:859 — separate @dataclass from SprintResult:1346 |
| payload_text | Marked as not found | EXISTS at duckdb_store.py:261 — optional LMDB-only field |
| SourceType Literal | Marked as no centralized Literal | Confirmed plain str across 62 files (design choice) |
| _run_sprint_mode | Marked as in sprint_scheduler.py | Actually in __main__.py:2680 |

---

*Generated: 2026-05-22 from live source analysis*
*Sources: __main__.py, core/__main__.py, runtime/sprint_scheduler.py, knowledge/duckdb_store.py, project_types.py*
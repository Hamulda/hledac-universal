<!-- ⚠️ ARCHITECTURE.md last updated: Tue May 12 16:46:28 2026 +0200 -->
<!-- Sprint F234 added: IPFS sidecar, BGP sidecar, dynamic KV cache -->

# hledac/universal Architecture

## Entry Points

| Entry Point | File | Purpose |
|-------------|------|---------|
| `run_sprint()` | `core/__main__.py:869` | Canonical sprint entry point, async coroutine |
| `main()` | `core/__main__.py:1876` | CLI dispatcher |
| `run_ct_pivot()` | `core/__main__.py:1816` | Single CT domain pivot |
| `run_semantic_pivot()` | `core/__main__.py:1845` | Semantic search pivot |
| `SprintScheduler` | `runtime/sprint_scheduler.py:1240` | Core execution engine (11,874 lines) |

## SprintScheduler Lifecycle

```
run_sprint() → SprintScheduler.__init__() → run() [loop]
                                         ↓
                          _run_one_cycle() / _run_one_cycle_aggressive()
                                         ↓
                    ┌────────────────────┴────────────────────┐
                    ↓                    ↓                    ↓
            _run_feed_branch()    _run_public_branch()  _run_ct_branch()
                    ↓                    ↓                    ↓
            Live feed polling     Public discovery     CT log discovery
                                                     + PDNS pivot
                                                     + Wayback prelude
                                                     + DOH prelude
```

## Lane Pipeline (Acquisition Lanes)

### Core Lanes (always active)
| Lane | Method | Source |
|------|--------|--------|
| `FEED` | `_run_feed_branch()` | Live feed polling |
| `PUBLIC` | `_run_public_branch()` | Public discovery in cycle |
| `CT` | `_run_ct_branch()` | Certificiate Transparency logs |

### Prelude Lanes (memory-gated, nonfeed)
| Lane | Method | Gate | Advisory |
|------|--------|------|----------|
| `WAYBACK` | `_run_wayback_prelude_lane()` | memory ok/warn | `wayback_cdx` |
| `PASSIVE_DNS` | `_run_pdns_prelude_lane()` | memory ok | `passive_dns` |
| `DOH` | `_run_doh_prelude_lane()` | memory ok | `doh_lane` |
| `PIVOT_EXECUTOR` | inline in `_run_lane()` | N/A | `pivot_planner` advisory |

### Key Constants
| Constant | Value | Location |
|----------|-------|----------|
| `_MAX_FINDINGS_PER_SPRINT` | 500 | line 1346 |
| `MAX_LANE_REJECTIONS` | 1000 | line 52 |
| `MAX_GC_STATS` | 1000 | line 56 |
| `MAX_MEMORY_ENTITIES` | bounded | line 94 |
| `MAX_MEMORY_EXPOSURES` | bounded | line 95 |
| `MAX_MEMORY_PIVOTS` | bounded | line 96 |
| `_MAX_BRANCH_TIMEOUT_CAP` | 300.0s | line 644 |
| `MAX_SOURCE_FAMILY_EVENTS` | 200 | line 1056 |

## Advisory Sidecars (fire-and-forget, `return_exceptions=True`)

| Sidecar | Method | Sprint |
|---------|--------|-------|
| `IdentityStitchingAdapter` | `_run_identity_stitching_sidecar()` | F202B |
| `LeakSentinelAdapter` | `_run_leak_sentinel_sidecar()` | F202D |
| `TemporalArchaeologistAdapter` | `_run_temporal_archaeology_sidecar()` | F202E |
| `EvidenceTriageCoordinator` | `_run_evidence_triage_sidecar()` | F202I |
| `SprintDiffEngine` | lazy import | F203A |
| `KillChainTagger` | lazy import | F203C |
| `WaybackDiffMiner` | lazy import | F203F |
| `PivotPlanner` | `_run_pivot_planner_advisory()` | F202G |
| `M1ResourceGovernor` | evaluated in loop | F202J |
| `PrefetchOracle` | advisory only | F200A |

## Fail-Soft Contract

Every sidecar/advisory wrapped in `try/except Exception` with `return_exceptions=True`:

```python
# Pattern (line 2328):
await asyncio.gather(*self._bg_tasks, return_exceptions=True)
```

Sidecar exceptions logged but never propagate:
```python
# Pattern (line 855-878):
sidecars_skipped: tuple[str, ...] = ()
# Sidecars set skipped when RAM pressure or exception
```

## GHOST_INVARIANTS (SprintScheduler)

| Invariant | Implementation | Location |
|-----------|----------------|----------|
| `time.time()` → `_time.time()` | Module-level alias | runtime/sprint_scheduler.py |
| `time.monotonic()` → `_time.monotonic()` | Used for all elapsed calculations | lines 1703, 1769, etc. |
| `gather(return_exceptions=True)` | All concurrent gather calls | line 2328 |
| `try/except` on all sidecar calls | Exception isolation | `_run_*_sidecar()` methods |
| New nonfeed lane → `_run_*_prelude_lane()` | Separated from main loop | lines 4351-4431 |
| Branch timeout cap | `min(config_timeout, remaining_s * 0.5, MAX_BRANCH_TIMEOUT_CAP)` | line 5094 |

## Memory Budget

| Component | Budget | Guard |
|-----------|--------|-------|
| Findings | `_MAX_FINDINGS_PER_SPRINT` (500) | hard cap |
| Lane rejections | `MAX_LANE_REJECTIONS` (1000) | eviction |
| GC stats | `MAX_GC_STATS` (1000) | ring buffer |
| Memory entities | `MAX_MEMORY_ENTITIES` | slice cap |
| Memory exposures | `MAX_MEMORY_EXPOSURES` | slice cap |
| Memory pivots | `MAX_MEMORY_PIVOTS` | slice cap |

## SourceTier Hierarchy

```python
class SourceTier(IntEnum):
    LIVE = auto()       # real-time feeds
    CT = auto()          # certificate transparency
    PUBLIC = auto()      # public sources
    ARCHIVE = auto()     # wayback/archive feeds
```

## Key Data Structures

| Structure | File | Purpose |
|-----------|------|---------|
| `SprintSchedulerConfig` | line 624 | Configuration dataclass |
| `SprintSchedulerResult` | line 693 | Outcome telemetry |
| `AcquisitionLane` | line 570 | Lane enum (FEED, CT, WAYBACK, PDNS, DOH, PIVOT) |
| `SprintScheduler` | line 1240 | Main scheduler class |
| `SidecarRunOutcome` | | Advisory execution result |

## Critical Paths

### CT Branch (line 5339)
```
_run_ct_branch()
  → _run_ct_predispatch()        # F234 prelive gate
  → _run_ct_log_discovery_in_cycle()  # CT log scan
  → _run_ct_to_passivedns_active_pivot()  # PDNS pivot
  → _run_pdns_for_domain()       # per-domain PDNS lookup
```

### Nonfeed Prelude Gather (line 4263)
```
_run_nonfeed_prelude_gather()
  → _run_wayback_prelude_lane()   # if WAYBACK shaped
  → _run_pdns_prelude_lane()      # if PASSIVE_DNS shaped
  → _run_doh_prelude_lane()       # if DOH shaped
  → PIVOT_EXECUTOR inline         # structured already
```

### Branch Timeout Budget (line 5094)
```
Formula: min(config_timeout, remaining_s * 0.5, MAX_BRANCH_TIMEOUT_CAP)
- Capped at MAX_BRANCH_TIMEOUT_CAP (300s) to bound worst case
- Branches skipped with reason logged when budget exhausted
```

## Known Issues / Technical Debt

| ID | Issue | Location | Status |
|----|-------|----------|--------|
| TODO D6 | PUBLIC lane timeout does not release per-lane budget | line 4989 | Open |
| F219M | GC telemetry bounded to `MAX_GC_STATS` | line 56 | Fixed |
| F234A | DOH adapter integrated | line 1425 | Active |

## Key Imports / Seams

| Seam | Module | Purpose |
|------|--------|---------|
| `FetchCoordinator` | `coordinators/fetch_coordinator.py` | HTTP transport (curl_cffi) |
| `DuckDBStore` | `knowledge/duckdb_store.py` | Canonical write |
| `GraphService` | `knowledge/graph_service.py` | DuckPGQ entity memory |
| `MLX` | `utils/mlx_cache.py` | Metal inference |
| `SidecarBus` | `runtime/sidecar_bus.py` | Sidecar communication |
| `DOHAdapter` | `intelligence/doh_lane.py` | DNS-over-HTTPS lane |

## File Statistics

| File | Lines | Purpose |
|------|-------|---------|
| `runtime/sprint_scheduler.py` | 11,874 | Core scheduler |
| `core/__main__.py` | 1,950 | Entry points |
| `coordinators/fetch_coordinator.py` | ~700 | HTTP transport |
| `knowledge/duckdb_store.py` | ~600 | Canonical storage |
| `brain/hermes3_engine.py` | ~500 | LLM inference |

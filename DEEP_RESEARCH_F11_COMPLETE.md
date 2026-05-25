# DEEP_RESEARCH_F11_COMPLETE.md — Sprint F11 Deep Research Integration

**Date:** 2026-05-23
**Status:** COMPLETE — UnifiedResearchEngine wired as optional post-sprint advisory

---

## 1. Architecture Decision

`UnifiedResearchEngine` (enhanced_research.py, 117KB) is wired as an **opt-in post-sprint advisory** on the canonical sprint pipeline. It is **NOT instantiated as a singleton** — each deep research invocation creates a fresh engine instance inside `deep_research_provider_seam()`.

**Wiring location:** `runtime/sprint_scheduler.py` — TEARDOWN phase, after `_sidecar_orchestrator.run_advisory_runner()` and before Hermes unload.

---

## 2. API Contract (verified 2026-05-23)

### UnifiedResearchEngine.__init__
```python
def __init__(
    self,
    config: Optional[UnifiedResearchConfig] = None,
    research_config: Optional[ResearchConfig] = None,
) -> None
```
- All sub-engines lazy-loaded (M1 8GB optimization)
- No singleton pattern — engine is created per-call inside `deep_research_provider_seam()`

### deep_research_provider_seam (PUBLIC ENTRYPOINT)
```python
async def deep_research_provider_seam(
    request: DeepResearchRequest,
    grounding: Optional[DeepResearchGroundingShim] = None,
) -> DeepResearchResponse
```
Defined at enhanced_research.py:2727.

### DeepResearchRequest (required fields)
| Field | Type | Default |
|-------|------|---------|
| `query` | `str` | **required** |
| `depth` | `ResearchDepth` | `ResearchDepth.ADVANCED` |
| `query_type` | `Optional[QueryType]` | `None` |
| `max_results` | `int` | `50` |
| `grounding_hints` | `Optional[Dict[str, List[str]]]` | `None` |

`ResearchDepth` enum values: `BASIC`, `ADVANCED`, `DEEP`, `EXHAUSTIVE`, `AUTONOMOUS`

### DeepResearchResponse
| Field | Type |
|-------|------|
| `findings` | `List[ResearchFinding]` |
| `fused_results` | `List[Dict[str, Any]]` |
| `confidence_score` | `float` |
| `execution_time_seconds` | `float` |
| `sources_used` | `List[str]` |
| `tools_executed` | `List[str]` |

### ResearchFinding fields
| Field | Type |
|-------|------|
| `url` | `Optional[str]` |
| `source_type` | `str` |
| `timestamp` | `datetime` |
| `relevance_score` | `float` |
| `credibility_score` | `float` |
| `temporal_relevance` | `Optional[datetime]` |
| `metadata` | `Dict[str, Any]` |

---

## 3. Constructor Lifecycle

**No singleton required.** `deep_research_provider_seam()` creates `UnifiedResearchEngine` internally per call:
- Engine initialized fresh each invocation
- Lazy sub-engines (AcademicSearchEngine, ArchiveDiscovery, etc.) loaded on first use
- No persistent state between calls
- `SprintScheduler.__init__` does NOT instantiate `UnifiedResearchEngine` — it is never stored as `self._engine` or similar

---

## 4. Wire Location & Flow

```
SprintScheduler.run() TEARDOWN section (line ~3348):
  self._maybe_launch_enhanced_research()   # fire-and-forget, no await

_maybe_launch_enhanced_research() (line ~10311):
  - Gate: self._config.deep_research_enabled (set by --deep-research flag)
  - Memory guard: snapshot.is_warn/is_critical/is_emergency → skip
  - Creates _run_enhanced_research_async() task
  - Adds to _background_research_tasks set

_run_enhanced_research_async() (line ~10329):
  - asyncio.wait_for(self._run_enhanced_research(), timeout=180.0)
  - CancelledError propagated; TimeoutError caught; all other exceptions caught

_run_enhanced_research() (line ~10339):
  GATES (any fail → return []):
  1. enhanced_research import succeeds
  2. self._config.deep_research_enabled == True
  3. accepted_findings >= 3 (new F11 gate)
  4. RAM guard: `is_warn` OR `is_critical` OR `is_emergency` blocks (using `snap.get()` dict lookup)
  Then:
  - depth = EXHAUSTIVE if self._config.extreme_mode else DEEP
  - req = DeepResearchRequest(query, depth, max_results=50, grounding_hints=[])
  - res: DeepResearchResponse = await deep_research_provider_seam(req, None)
  - res.findings[:100] → CanonicalFinding list
  - DuckDB ingest via duckdb_store.async_ingest_findings_batch()
```

---

## 5. Gate Summary

| Gate | Condition | Source |
|------|-----------|--------|
| Feature flag | `HLEDAC_ENABLE_DEEP_RESEARCH=1` OR `--deep-research` | `self._config.deep_research_enabled` |
| Minimum findings | `accepted_findings >= 3` | NEW F11 gate |
| RAM cap | `is_warn` OR `is_critical` OR `is_emergency` blocks | Uses existing `snap.get()` dict keys |
| Timeout | `asyncio.wait_for(..., timeout=180.0)` | GHOST_INVARIANT |

Memory guard uses `snap.get('is_warn')` dict lookup (same threshold as `_maybe_launch`).

---

## 6. ResearchFinding → CanonicalFinding Conversion

```python
canonical = CanonicalFinding(
    finding_id=f"er_{getattr(f, 'id', 'unknown')}",
    query=f"deep_research:{getattr(self._result, 'sprint_id', 'unknown')}",
    source_type=getattr(f, 'source_type', src) or src,
    confidence=getattr(f, 'relevance_score', 0.5) * getattr(f, 'credibility_score', 0.5),
    ts=ts_float,
    provenance=getattr(f, 'url', '') or '',
    payload_text=f"{title}\n{content[:2000]}",
)
```
Bounded to 100 findings (`MAX_DEEP_RESEARCH_FINDINGS=100`).

---

## 7. Assumptions

1. **Lazy loading is M1-safe**: `UnifiedResearchEngine` lazy-loads all sub-engines; the memory guard at 75% prevents OOM.
2. **`deep_research_provider_seam` is async**: Uses `asyncio.timeout()` internally (NOT `asyncio.run()`).
3. **`duckdb_store` availability**: `_duckdb_store` is set during `run()` — accessible at teardown.
4. **`accepted_findings` field exists**: `SprintSchedulerResult` has `accepted_findings: int` field.
5. **No `TriadAdmissionDescriptor` import needed**: Removed from imports (unused dead code stub).

---

## 8. File Changes

| File | Change |
|------|--------|
| `runtime/sprint_scheduler.py` | Added `accepted_findings >= 3` gate and corrected RAM guard to use `snap.get('is_warn')` dict keys in `_run_enhanced_research()`. Removed unused `TriadAdmissionDescriptor` import. |
| `core/__main__.py` | Already has `--deep-research` and `--extreme` flags (pre-existing). |
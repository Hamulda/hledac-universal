# ENHANCED_RESEARCH_WIRING.md — Sprint F11 Deep Research Seam

**Date:** 2026-05-23
**Status:** DORMANT → CANONICAL WIRING PROPOSED

---

## 1. Architecture Decision

`UnifiedResearchEngine` (enhanced_research.py, 117KB, 18 classes) is the canonical lazy provider seam for deep research. It is NOT activated by default — wired as an **opt-in post-sprint advisory** on the canonical sprint pipeline.

**Wiring location:** `runtime/sprint_scheduler.py` — post-WINDUP entry (after `_flush_dedup()` and enrichment flush), before `_finalize_result_truth()`.

**Activation gate:** `--deep-research` CLI flag OR `HLEDAC_ENABLE_DEEP_RESEARCH=1` env var, **AND** research mode preset IN `{DEEP, EXTREME, AUTONOMOUS}`, **AND** memory pressure < 75% (uma.is_warn check).

---

## 2. Input Contract — DeepResearchRequest Construction

```python
# In sprint_scheduler.py — new method _build_deep_research_request()
req = DeepResearchRequest(
    query=query,
    depth=ResearchDepth.EXHAUSTIVE,   # map from config preset
    query_type=QueryType.OSINT,        # inferred from sprint
    max_results=50,                   # bounded
    grounding_hints={                # from sprint result
        "topics": [query],             # SprintSchedulerResult holds counts only;
                                      # query seed is sole topic hint (Option A)
        "domains": [],
    },
    triad_admission=TriadAdmissionDescriptor(
        provider_candidate="UnifiedResearchEngine",
        triad_authority_exists=True,    # pre-declared
        deepresearch_napojen=True,
    ),
)
```

**ResearchDepth mapping:**

| Preset | Depth |
|--------|-------|
| DEEP | EXHAUSTIVE |
| EXTREME | EXHAUSTIVE |
| AUTONOMOUS | EXHAUSTIVE |
| others | NOT ACTIVATED |

---

## 3. Output Contract — ResearchFinding → CanonicalFinding

`deep_research_provider_seam()` returns `DeepResearchResponse`:
```python
@dataclass
class DeepResearchResponse:
    findings: List[ResearchFinding]
    fused_results: List[Dict[str, Any]]
    confidence_score: float
    execution_time_seconds: float
    sources_used: List[str]
    tools_executed: List[str]
```

**ResearchFinding fields (enhanced_research.py:224):**
```python
@dataclass
class ResearchFinding:
    id: str
    title: str
    content: str
    url: Optional[str]
    src: str              # Tool that found it ← confirmed field name
    source_type: str
    timestamp: datetime
    relevance_score: float = 0.0
    credibility_score: float = 0.5
    temporal_relevance: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
```

**DuckDB CanonicalFinding schema (duckdb_store.py:253):**
```python
class CanonicalFinding(msgspec.Struct, frozen=True, gc=False):
    finding_id: str
    query: str
    source_type: str
    confidence: float
    ts: float                    # Unix timestamp (float), NOT ISO string
    provenance: tuple[str, ...] = ()
    payload_text: str | None = None
```

**Conversion (confirmed correct field names):**
```python
from knowledge.duckdb_store import CanonicalFinding

def _research_finding_to_canonical(f: ResearchFinding, sprint_id: str) -> CanonicalFinding:
    return CanonicalFinding(
        finding_id=f.id,
        query=f"deep_research:{sprint_id}",
        source_type="deep_research",
        confidence=f.credibility_score,  # credibility → confidence
        ts=f.timestamp.timestamp(),        # datetime → Unix float
        provenance=(f"enhanced_research:{f.src}",),
        payload_text=f.content[:4096],  # content in payload_text
    )
```

**DuckDB ingest signature (duckdb_store.py:4858):**
```python
async def async_ingest_findings_batch(
    self, findings: list[CanonicalFinding]
) -> list[FindingQualityDecision | ActivationResult]
```

---

## 4. DuckDB Ingest Seam

**Store:** `self._duckdb_store` (DuckDBShadowStore, set in `__init__`)

**Call site (sprint_scheduler.py):**
```python
# After enrichment flush, before _finalize_result_truth()
dr_results = await self._run_deep_research_advisory(
    query, self._duckdb_store, sprint_id
)
if dr_results and dr_results.findings:
    canonicals = [
        self._research_finding_to_canonical(f, sprint_id)
        for f in dr_results.findings[:100]  # MAX_DEEP_RESEARCH_FINDINGS=100
        if f
    ]
    if canonicals and hasattr(self._duckdb_store, "async_ingest_findings_batch"):
        try:
            await self._duckdb_store.async_ingest_findings_batch(canonicals)
        except Exception as e:
            logger.W(f"[DEEP_RESEARCH] DuckDB ingest failed: {e}")
```

Note: Return value `list[FindingQualityDecision | ActivationResult]` is not stored — advisory-only telemetry is out of scope.

---

## 5. Fail-Soft Wrapper (GHOST_INVARIANTS Compliant)

```python
async def _run_deep_research_advisory(
    self,
    query: str,
    duckdb_store: Any,
    sprint_id: str,
) -> Optional[DeepResearchResponse]:
    """
    Post-sprint deep research advisory.
    Gated by --deep-research flag, DEEP/EXTREME/AUTONOMOUS preset, memory < 75%.
    Fail-soft: re-raises CancelledError; logs and returns None for all other
    exceptions. Sprint result is unaffected.
    """
    try:
        # Memory guard — 75% threshold (stricter than multimodal 85%)
        from utils.uma_budget import get_uma_snapshot
        snapshot = get_uma_snapshot()
        if snapshot.is_warn or snapshot.is_critical or snapshot.is_emergency:
            logger.I("[DEEP_RESEARCH] Skipped — memory pressure")
            return None

        # Mode gate
        mode = getattr(self._config, 'research_mode', None)
        if mode not in (ResearchMode.DEEP, ResearchMode.EXTREME, ResearchMode.AUTONOMOUS):
            return None

        # Build request — query as sole grounding topic
        req = self._build_deep_research_request(query)
        from enhanced_research import deep_research_provider_seam
        resp = await deep_research_provider_seam(req)
        return resp
    except asyncio.CancelledError:
        raise  # GHOST_INVARIANTS: propagate cancellation
    except Exception as e:
        logger.W(f"[DEEP_RESEARCH] Advisory failed: {e}")
        return None
```

Note: `except Exception as e:` (named) is not bare `except:` — compliant with GHOST_INVARIANTS. Cancellation is explicitly re-raised before the broad catch.

---

## 6. CLI Integration

**core/__main__.py** — add `--deep-research` flag:
```python
# In run_sprint() call:
run_sprint(args.query, float(args.duration), args.export_dir, args.aggressive,
           args.deep_probe, args.deep_research,
           acquisition_profile=args.acquisition_profile)
```

**SprintSchedulerConfig** — add field:
```python
deep_research_enabled: bool = False
```

---

## 7. GHOST_INVARIANTS Compliance

| Invariant | Compliance |
|-----------|------------|
| Never exceed 6.25GB RAM | 75% memory guard blocks activation under pressure |
| No asyncio.to_thread for DuckDB | Direct await on `async_ingest_findings_batch` |
| Use time.monotonic() | Timing in deep_research_provider_seam uses monotonic |
| Fail-safe | try/except + return None on failure; CancelledError propagated |
| gather return_exceptions=True | N/A (sequential advisory, not parallel gather) |
| mx.eval([]) before clear_cache | N/A (no MLX in deep research advisory) |
| Propagate CancelledError | Explicit re-raise before broad except |

---

## 8. Assumptions & Open Questions

**Assumptions (confirmed):**
1. `ResearchFinding.src` (not `source_type`) — confirmed at enhanced_research.py:230
2. `ResearchFinding.credibility_score` → `CanonicalFinding.confidence` — confirmed
3. `ResearchFinding.timestamp.timestamp()` → `CanonicalFinding.ts` (Unix float) — confirmed
4. `triad_admission` is hard-coded dormant descriptor — awaiting F11 triad connection
5. `deep_research_provider_seam()` is async, returns `DeepResearchResponse` — confirmed
6. `CanonicalFinding` is `msgspec.Struct` — must construct via struct, not dict

**Open questions:**
1. **`SprintSchedulerResult` has NO finding objects** — only int count fields (`public_accepted_findings`, `ct_log_accepted_findings`, `lane_*_accepted_findings`). Query seed is the grounding topic (Option A). DuckDB IOC extraction (Option B) adds latency and is deferred.
2. F11 triad activation timeline — `TriadAdmissionDescriptor` remains dormant until then
3. `LocalCorpusConsumerDescriptor` consumer seam — not wired (LOCAL_CORPUS source family not available)
4. `MAX_DEEP_RESEARCH_FINDINGS=100` bound — recommended

---

## 9. File Changes Summary

| File | Change |
|------|--------|
| `runtime/sprint_scheduler.py` | Add `_run_deep_research_advisory()`, `_build_deep_research_request()`, `_research_finding_to_canonical()`, wire in WINDUP phase, add `deep_research_enabled` to config |
| `core/__main__.py` | Add `--deep-research` CLI flag, pass to `run_sprint()` |

**No changes to:**
- `enhanced_research.py` — remains dormant canonical provider
- `knowledge/duckdb_store.py` — canonical write path unchanged
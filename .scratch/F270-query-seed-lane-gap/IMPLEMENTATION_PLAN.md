# F270 Implementation Plan: Query→Seed→Lane Flow

## Overview

Fix the architectural gap where `build_acquisition_plan()` runs BEFORE domain seeds are extracted, causing CT lane to be disabled for conceptual queries like "ALPHV BlackCat ransomware".

## Changes

### 1. `runtime/acquisition_strategy.py`

#### 1a. Extend `NonfeedSeedContext` (L830)

Add hermes-extracted entities field:

```python
@dataclass
class NonfeedSeedContext:
    """
    F222I: Bounded seed context for nonfeed lane query shaping.
    F270: Extended with hermes_extracted_entities for semantic enrichment.
    """
    domains: tuple[str, ...] = ()
    ips: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()
    hermes_extracted_entities: tuple[str, ...] = ()  # F270: NEW
    ct_search_hints: tuple[str, ...] = ()             # F270: NEW
```

#### 1b. Add `pre_seed_context` param to `build_acquisition_plan()` (L2940)

```python
def build_acquisition_plan(
    query: str,
    duration_s: float,
    aggressive_mode: bool,
    uma_state: dict[str, float] | None,
    swap_detected: bool,
    accepted_findings_so_far: Sequence[CanonicalFinding],
    branch_timeout_count: int,
    acquisition_profile: str = "",
    source_quality_weights: Mapping[str, float] | None = None,
    rl_lane_combo: frozenset[str] | None = None,
    pre_seed_context: NonfeedSeedContext | None = None,  # F270: NEW
    # ... rest of params
) -> AcquisitionPlan:
```

#### 1c. Update `has_domain` detection (L3057)

```python
    has_domain = _has_domain_or_ip(query)
    # F270: Also check pre-extracted domain seeds
    if pre_seed_context and pre_seed_context.domains:
        has_domain = True
    # F270: Hermes-extracted entities may indicate infrastructure
    if pre_seed_context and pre_seed_context.hermes_extracted_entities:
        has_domain = True
```

#### 1d. Update `build_lane_query()` for CT (L4483)

```python
    if lane == AcquisitionLane.CT:
        if seed_context and seed_context.domains:
            return seed_context.domains[0]
        # F270: Use Hermes-extracted entities for wildcard expansion
        if seed_context and seed_context.hermes_extracted_entities:
            return seed_context.hermes_extracted_entities[0]
        domains = _DOMAIN_OR_IP_RE.findall(base_query)
        if domains:
            unique = list(dict.fromkeys(domains))[:5]
            return " ".join(unique)
        return base_query  # F265D-FIX: wildcard expansion
```

---

### 2. `runtime/sprint_scheduler.py`

#### 2a. Pre-seed extraction before build_acquisition_plan (around L6690)

Add before `self._acquisition_plan = build_acquisition_plan(...)`:

```python
# F270: Extract domain seeds BEFORE build_acquisition_plan
from hledac.universal.runtime.nonfeed_candidate_ledger import (
    extract_domain_candidates_from_text,
)
_pre_candidates = extract_domain_candidates_from_text(query)
_pre_seed_ctx = NonfeedSeedContext(
    domains=tuple(c.domain for c in _pre_candidates if c.domain),
    ips=(),
    urls=(),
    hermes_extracted_entities=(),
    ct_search_hints=(),
)

self._acquisition_plan = build_acquisition_plan(
    ...,
    pre_seed_context=_pre_seed_ctx,  # F270: NEW
    ...
)
```

#### 2b. Hermes3 enrichment in `_run_mandatory_acquisition_prelude()` (around L12863)

Add after `extract_domain_candidates_from_text()`:

```python
# F270: Hermes3 semantic enrichment for queries without domain seeds
_hermes_enriched = False
if not _candidates and not _seed_ctx.domains:
    try:
        _hermes_engine = getattr(self, '_hermes_engine', None)
        if _hermes_engine:
            from hledac.universal.runtime.nonfeed_candidate_ledger import (
                hermes_enhanced_seed_extraction,
            )
            _enriched_ctx = await hermes_enhanced_seed_extraction(
                query=query,
                hermes_engine=_hermes_engine,
                seed_ctx=_seed_ctx,
                timeout_s=5.0,
            )
            if _enriched_ctx.hermes_extracted_entities:
                _seed_ctx = _enriched_ctx
                _hermes_enriched = True
    except Exception:
        pass  # fail-soft

if _hermes_enriched:
    self._result.pivot_seed_hermes_enriched = True
```

---

### 3. `runtime/nonfeed_candidate_ledger.py`

#### Add `hermes_enhanced_seed_extraction()` (after L880)

```python
async def hermes_enhanced_seed_extraction(
    query: str,
    hermes_engine: Any,
    seed_ctx: NonfeedSeedContext,
    timeout_s: float = 5.0,
) -> NonfeedSeedContext:
    """
    F270: Use Hermes3 for semantic entity extraction from conceptual queries.
    
    M1 8GB: Lazy MLX load, max 512 tokens, 5s timeout, fail-soft.
    """
    if not hermes_engine or seed_ctx.domains:
        return seed_ctx
    
    try:
        _prompt = (
            f"Analyze this OSINT query and extract infrastructure indicators.\n"
            f"Query: {query}\n\n"
            f"Extract threat actor names, malware families, infrastructure terms.\n"
            f"Format: comma-separated list, max 10 items"
        )
        
        _result = await asyncio.wait_for(
            hermes_engine.generate(_prompt, max_tokens=128),
            timeout=timeout_s,
        )
        
        _entities = [e.strip() for e in (_result or '').split(',') if e.strip()][:10]
        
        return NonfeedSeedContext(
            domains=seed_ctx.domains,
            ips=seed_ctx.ips,
            urls=seed_ctx.urls,
            hermes_extracted_entities=tuple(_entities),
            ct_search_hints=tuple(_entities[:3]),
        )
        
    except (asyncio.TimeoutError, Exception):
        return seed_ctx
```

---

## Files Summary

| File | Change Type | Lines |
|------|-------------|-------|
| `runtime/acquisition_strategy.py` | Modify | +15 |
| `runtime/sprint_scheduler.py` | Modify | +35 |
| `runtime/nonfeed_candidate_ledger.py` | Add function | +60 |
| `tests/probe_f270_query_seed_flow/` | Add tests | ~200 |

## Verification

```bash
pytest tests/probe_f270_query_seed_flow/ -x -v
```
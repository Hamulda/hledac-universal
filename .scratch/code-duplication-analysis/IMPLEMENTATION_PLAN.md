# Code Duplication — F360M Implementation
## Phase 1: Quick Wins

**Datum:** 2026-07-31  
**Status:** ✅ Phase 1 COMPLETE (Proof-of-Concept)

---

## Completed: GenericSidecarAdapter + CorrelateBasedSidecarAdapter

**Files Modified:**
- `runtime/sidecar_protocol.py` — Added GenericSidecarAdapter + CorrelateBasedSidecarAdapter classes
- `runtime/sidecar_protocol_adapters.py` — GitHubGistSidecarAdapter, PassiveFingerprintSidecarAdapter, PassiveTechStackSidecarAdapter migrated

**Co bylo implementováno:**

```python
# GenericSidecarAdapter — extract → search → transform pattern
class GenericSidecarAdapter(BaseSidecarAdapter):
    def extract_terms(self, ctx: SidecarContext) -> list[str]: ...
    async def search(self, terms: list[str], ctx: SidecarContext) -> list[Any]: ...
    def result_to_finding(self, result: Any, ctx: SidecarContext) -> dict | None: ...
    async def run_async(self, ctx: SidecarContext) -> list[Any]: ...

# CorrelateBasedSidecarAdapter — correlate(findings, query) pattern
class CorrelateBasedSidecarAdapter(BaseSidecarAdapter):
    def create_adapter(self) -> Any: ...  # factory method
    async def run_async(self, ctx: SidecarContext) -> list[Any]: ...
```

**Migrated Adapters:**
| Adapter | Base Class | Reduction |
|---------|-----------|-----------|
| GitHubGistSidecarAdapter | GenericSidecarAdapter | 65 → 40 LOC |
| PassiveFingerprintSidecarAdapter | CorrelateBasedSidecarAdapter | 45 → 25 LOC |
| PassiveTechStackSidecarAdapter | CorrelateBasedSidecarAdapter | 45 → 25 LOC |

**Benefit:**
- 3 adapters migrated in this session
- ~65 LOC saved across 3 adapters
- CorrelateBasedSidecarAdapter handles `correlate()` pattern that GenericSidecarAdapter couldn't cover

---

## Adaptery vhodné pro migraci na GenericSidecarAdapter

| Adapter | Status | Důvod pro migraci |
|---------|--------|-------------------|
| FediverseSidecarAdapter | ✅ Lze migrovat | extract_terms + search + result_to_finding |
| DHTSidecarAdapter | ✅ Lze migrovat | jednoduchý vzor |
| AcademicSidecarAdapter | ⚠️ Částečně | specifické filtry per source |
| AltProtocolSidecarAdapter | ⚠️ Částečně | více search metod současně |
| LeakSentinelSidecarAdapter | ✅ Lze migrovat | extract_targets + scan_all_sources |
| TVNewsSidecarAdapter | ✅ Lze migrovat | jednoduchý vzor |
| PassiveFingerprintSidecarAdapter | ✅ Lze migrovat | correlate() → findings |
| PassiveTechStackSidecarAdapter | ✅ Lze migrovat | correlate() → findings |
| SocialIdentityMinerSidecarAdapter | ✅ Wiring-only | vrací [], migrace zbytečná |
| IdentityStitchingSidecarAdapter | ✅ Wiring-only | vrací [], migrace zbytečná |
| TemporalArchaeologySidecarAdapter | ✅ Wiring-only | vrací [], migrace zbytečná |
| LanceDBRAGSidecarAdapter | ⚠️ Složitější | 2-phase (index + search) |
| GitHubGistSidecarAdapter | ✅ Lze migrovat | extract_terms + search + transform |
| JA4CollectorSidecarAdapter | ✅ Lze migrovat | extract_domains + batch_ja4 |
| WhoisSidecarAdapter | ⚠️ Složitější | konfigurace service + více polí |
| ThreatIntelSidecarAdapter | ⚠️ Složitější | více feed sources najednou |

---

## Migrace Example: FediverseSidecarAdapter

**Původní kód (~100 LOC):**
```python
@SidecarRegistry.register("fediverse")
class FediverseSidecarAdapter(BaseSidecarAdapter):
    sidecar_id: str = "fediverse"
    lane_id: str = "fediverse"
    ram_budget_mb: int = 50
    priority: int = 6
    
    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        if not ctx.findings and not ctx.query:
            return []
        try:
            adapter = self._adapter_factory()
            search_terms = self._extract_search_terms(ctx)
            # ... 80+ LOC další logiky
        except Exception:
            return []
    
    def _extract_search_terms(self, ctx) -> list[str]:
        # ... 15 LOC
        pass
```

**Nový kód (~40 LOC):**
```python
@SidecarRegistry.register("fediverse")
class FediverseSidecarAdapter(GenericSidecarAdapter):
    sidecar_id: str = "fediverse"
    lane_id: str = "fediverse"
    ram_budget_mb: int = 50
    priority: int = 6
    
    def extract_terms(self, ctx: SidecarContext) -> list[str]:
        terms: list[str] = []
        for finding in ctx.findings[:20]:
            val = getattr(finding, "ioc_value", None)
            if val and len(val) < 100:
                terms.append(val)
        return terms[:10]
    
    async def search(self, terms: list[str], ctx: SidecarContext) -> list[Any]:
        adapter = self._adapter_factory()
        return await adapter.search_multiple_instances(terms)
    
    def result_to_finding(self, result: Any, ctx: SidecarContext) -> dict | None:
        post = getattr(result, "posts", [result])[0]
        return {
            "source_type": "fediverse",
            "query": ctx.query,
            "ioc_type": "social_media_post",
            "ioc_value": getattr(post, "url", ""),
            # ...
        }
```

---

## Phase 2: acquisition_strategy_planner dedup

**Status:** BLOCKED — Komplexní architekturní závislosti

**Problém:**
- `lanes/__init__.py` IMPORUJE z `acquisition_strategy_planner.py` (ne naopak)
- `acquisition/_lane_helpers.py` má OD LIŠNOU signaturu `lane_is_terminal(lane_name: str)`
- 3-úrovňová duplikace vyžadující pečlivou analýzu závislostí

**Akce:** Vyžaduje plánování a test suite. Aktuálně nelze provést bez risku regrese.

---

## Phase 3: source_finding_bridge template method

**Status:** ANALYZED — Realistický odhad: ~30-40% LOC reduction

**Aktuální stav (3147 LOC):**
| Function | LOC | Purpose |
|----------|-----|---------|
| ct_results_to_findings | ~219 | Certificate Transparency |
| wayback_results_to_findings | ~144 | Wayback Machine |
| passive_dns_results_to_findings | ~464 | Passive DNS |
| doh_results_to_findings | ~591 | DNS-over-HTTPS |
| rdap_result_to_findings | ~208 | RDAP |
| academic_results_to_findings | ~139 | Academic |
| network_recon_result_to_findings | ~646 | Network recon |

**Klíčový nález:** `_canonical_finding()` (33 LOC) je JIŽ sdílený helper. Variation je v:
1. Input validation (různé attrs: `hits`, `change_events`, `ips`, etc.)
2. Field extraction (specifické per-source)
3. Filtering rules (per-source rejection criteria)

**Realistický přínos:**
- Ne 83% jak se odhadovalo, ale ~30-40% (odhad: 3147 → ~2000 LOC)
- Důvod: každý bridge má specifickou field extraction logic která se nedá snadno generalizovat

**Doporučený přístup:**
1. Extrahovat `ResultToFinding` Protocol
2. Vytvořit `BaseBridge` s template method pro common validation + iteration
3. Specializace implementují: `extract_fields()`, `should_include()`, `build_telemetry()`
4. Zachovat `_canonical_finding` jako shared helper

**Alternativa (jednodušší):**
- Extrahovat sdílené helpers do samostatného modulu
- Některé `summarize_*` funkce mají podobný vzor → lze zjednodušit

---

## Phase 4: DuckDB Modularization (ALREADY IN PROGRESS)

**Status:** IN PROGRESS — F360 architecture plan defined, partial extraction done

**Již extrahováno:**
| Modul | LOC | Účel |
|-------|-----|------|
| `DuckDBBaseStore` | 280 | SDDÍLENÁ báze pro všechny DuckDB stores |
| `DuckDBQueryExecutor` | 528 | SQL construction + transaction framing |
| `duckdb_protocol.py` | 323 | `DuckDBStoreProtocol` — typed contract |
| `duckdb_ct_cache_store.py` | — | CT cache specialized store |
| `duckdb_forensics_store.py` | — | Forensics specialized store |

**Stále k rozbití:**
| Soubor | LOC | Poznámka |
|--------|-----|----------|
| `duckdb_store.py` | 10,752 | Hlavní monolith — postupně se rozkládá |

**Plánovaná architektura (z `duckdb_protocol.py`):**
```
duckdb_protocol.py  — Protocol (interface contract)
duckdb_canonical.py — Canonical SQL store (findings, runs, deltas)
duckdb_vector.py    — HNSW vector operations (RAG embeddings)
duckdb_wal.py       — WAL + LMDB lifecycle
duckdb_quality.py   — Stateful quality assessment
duckdb_analytics.py — Scorecard, FTS5, arrow metrics
duckdb_store.py     — DuckDBShadowStore (facade/wiring)
```

**Doporučené další kroky:**
1. Extrahovat `async_ingest_findings_batch` do samostatného modulu (největší a nejkomplexnější metoda)
2. Extrahovat graph attachment methods do `duckdb_graph_attachment.py`
3. Extrahovat vector/HNSW operations do `duckdb_vector.py`

---

## Phase 5: Cross-Store Protocol (NÍZKÁ PRIORITA)

**Status:** NOT NEEDED — LanceDB DEPRECATED, DuckDB je jediný backend

**Zjištění:**
- `LanceDBStore` je DEPRECATED od F350M-R
- Nahrazen DuckDB HNSW (žádný subprocess overhead, M1 8GB native)
- `duckdb_protocol.py` definuje `DuckDBStoreProtocol` jako jediný storage contract
- Graph má vlastní `GraphProtocol` (DuckPGQGraph + IOCGraph)

**Závěr:** Cross-store unification není aktuálně potřeba.

---

## Metrics Tracking

| Metric | Before | After | Target |
|--------|--------|-------|--------|
| Clone pairs (runtime/) | 1017 | — | 500 |
| Clone pairs (knowledge/) | 839 | — | 400 |
| Sidecar adapter LOC | ~2000 | ~1000 | 500 |
| DuckDB modularization | 10,752 | — | 5,000 |
| Overall duplication % | 35% | — | 25% |

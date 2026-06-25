# P0-1: Prelude Phase Catastrophe — Komplexní Analýza a Řešení

## Souhrn problému

```
INFO: [prelude] completed in 283.4s (budget=20s)
WARNING: [F223-D] prewindup barrier error
Active window: NEGATIVE — acquisition nikdy neběží
```

## Anatomie Prelude Phase

Z `runtime/sprint_scheduler.py:_run_internal()` (řádky ~6430-6790):

```python
async def _run_internal(...):
    # 1. _get_governor_uma() - SEQUENTIAL (ř. 6706-6708)
    _gov_task = asyncio.create_task(_get_governor_uma())
    _seeds_task = asyncio.create_task(_load_next_seeds())
    _uma_state, _swap_detected = await _gov_task           # ← čeká sekvenčně
    _next_seeds_ioc_seeds = await _seeds_task

    # 2. build_acquisition_plan() - SEQUENTIAL (ř. 6729-6767)
    #    TOTO JE 200+ sekund pro non-domain query
    self._timer.phase("acquisition_plan_build_start")
    self._acquisition_plan = build_acquisition_plan(
        query=query,
        duration_s=self._config.sprint_duration_s,
        ...
    )
    self._timer.phase("acquisition_plan_build_end")       # ← 200s+ ZDE

    # 3. Prewarm tasks - FIRE-AND-FORGET (správně, ř. 6082-6105)
    #    Běží v pozadí, neblokují

    # 4. DuckDB background writer - FIRE-AND-FORGET (ř. 6402)
    self._duckdb_writer_task = asyncio.create_task(...)

    # 5. _attempt_public_prewindup_barrier() - SEQUENTIAL (voláno PO build_acquisition_plan)
    await _attempt_public_prewindup_barrier(query)          # ← možná dalších 20s
```

## Root Cause Analysis

### RC1: `build_acquisition_plan` pro Non-Domain Query — 200s+

**Soubor:** `runtime/acquisition_strategy.py:3116-3250`

Pro non-domain query (např. "LockBit ransomware"):
1. `_has_domain_or_ip(query)` → False
2. `accepted_findings_so_far=0`, `feed_domain_seeds=()`, `synthetic_domains=()`
3. Vstupuje do větve `if not has_domain and accepted_findings_so_far > 0` → **False**, přeskakuje
4. Ale pak... **DOMAIN EXPANZE**:

```python
# Ř. 3194: P1-2: Also use _expand_query_keywords for keyword→domain expansion
_keyword_expansion = _expand_query_keywords(query)  # ← Volá se VŽDY

# Kód funkce (ř. 102-122):
def _expand_query_keywords(query: str) -> tuple[str, ...]:
    query_lower = query.lower()
    seeds: list[str] = []
    for keyword, domains in DOMAIN_EXPANSIONS.items():  # ← Iteruje 100+ klíčů
        if keyword in query_lower:                      # ← Pro "LockBit" najde match
            seeds.extend(domains)                        # ← Přidává DOMAINS
    return tuple(seeds[:5])
```

**Problém:** Pro každý matching keyword přidává domény. Pro "ransomware" je tam 10+ domén.
Pak následuje:
- `has_domain = True` (ř. 3202/3207)
- A pak se plánují **CT, DOH, WAYBACK, DNDS, PassiveDNS** lanes (všechny potřebují prewarm)
- A pak se volá `required_terminal_lanes()` která dělá další regex scanning

Navíc `DOMAIN_EXPANSIONS` má 100+ entries, každá s 5-15 domény. Pro pure "ransomware" query se matchuje 10+ keywords → 50+ domain seeds → plánování 5+ lanes.

### RC2: DuckDB Subprocess Initialization — 30-60s

**Soubor:** `knowledge/duckdb_subprocess_adapter.py:165-185`

```python
async def async_initialize(self) -> None:
    # Spawns subprocess
    self._proc = await asyncio.create_subprocess_exec(...)
    # Initializes schema
    await self._initialize_schema()  # ← 30-60s
```

Subprocess spawn + DuckDB init schema = ~30-60s.

### RC3: Prewarm Tasks — Špatné Řazení

**Soubor:** `runtime/sprint_scheduler.py:6056-6105`

```python
# Prewarm tasks jsou SPRÁVNĚ fire-and-forget (async to_thread)
# ALE: _prewarm_hermes_sync() volá asyncio.to_thread(
#     lambda: loop.run_until_complete(self._prewarm_hermes_for_sprint())
# )
# Který sám o sobě trvá 60-90s v THREADU
```

Problém: `_prewarm_hermes_sync` vytváří **nový event loop** v threadu a volá `run_until_complete`. Toto blokuje thread, ale protože je to `asyncio.to_thread`, hlavní event loop je volný.

**ALE:** Prewarm tasks start až PO `_get_governor_uma()` což je sekvenční.

Správné pořadí:
1. Start prewarm tasks OKAMŽITĚ (ne až po governor/plan)
2. DuckDB init v parallel
3. Governor evaluation v parallel
4. Build acquisition plan v parallel

### RC4: `_attempt_public_prewindup_barrier` — Dalších 20s+

**Soubor:** `runtime/sprint_scheduler.py:11346-11426`

Voláno po `build_acquisition_plan`. Pro domain query dělá:
- PUBLIC prewindup barrier check
- 10s timeout na live_public_pipeline

Pokud je query non-domain a DOMAIN EXPANZE způsobila, že se plánují PUBLIC lanes, barrier běží.

## Architektura: Kde je "Prelude" Měřeno

`INFO:hledac.universal.runtime.sprint_scheduler:[prelude] completed in 283.4s (budget=20s)` — toto je zřejmě z `_attempt_public_prewindup_barrier` nebo z `build_acquisition_plan` timing, NE z `_initialize_sprint_run`.

## Řešení

### Fix 1: Prewarm Tasks na Začátek (OKAMŽITĚ)

**Soubor:** `runtime/sprint_scheduler.py` — přesunout prewarm z `_initialize_sprint_run` na ÚPLNÝ ZAČÁTEK `_run_internal`

```python
async def _run_internal(...):
    # === P0-1 FIX: Start prewarm OKAMŽITĚ, ne po governor/plan ===
    from hledac.universal.utils.async_helpers import safe_create_task
    
    def _prewarm_hermes_sync() -> None:
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(self._prewarm_hermes_for_sprint())
            loop.close()
        except Exception:
            pass
    
    def _prewarm_modernbert_sync() -> None:
        try:
            from hledac.universal.brain.modernbert_engine import ModernBertEngine
            engine = ModernBertEngine()
            loop = asyncio.new_event_loop()
            loop.run_until_complete(engine.load())
            loop.close()
        except Exception:
            pass
    
    def _prewarm_mlx_embeddings_sync() -> None:
        try:
            from hledac.universal._shims.core_mlx_embeddings import get_embedding_manager
            mgr = get_embedding_manager()
            if mgr is not None and not mgr._is_loaded:
                mgr._load_model()
        except Exception:
            pass
    
    # Fire-and-forget: běží v thread pool, neblokuje event loop
    self._hermes_prewarm_task = safe_create_task(
        asyncio.to_thread(_prewarm_hermes_sync), name="hermes_prewarm_phase1"
    )
    safe_create_task(
        asyncio.to_thread(_prewarm_modernbert_sync), name="modernbert_prewarm"
    )
    safe_create_task(
        asyncio.to_thread(_prewarm_mlx_embeddings_sync), name="mlx_embed_prewarm"
    )
    
    # === TEPRVE TEĎ: Paralelní init ===
    _gov_task = asyncio.create_task(_get_governor_uma())
    _seeds_task = asyncio.create_task(_load_next_seeds())
    
    # DuckDB init v parallel s governor/seeds
    _duckdb_init_task = asyncio.create_task(self._init_duckdb_async())
    
    # Build acquisition plan v parallel
    _plan_task = asyncio.create_task(self._build_plan_async(query, ...))
    
    # Čekáme výsledky
    _uma_state, _swap_detected = await _gov_task
    _next_seeds = await _seeds_task
    _duckdb_ready = await _duckdb_init_task  # Pokud selže, pokračujeme bez DuckDB
    _acquisition_plan = await _plan_task
```

### Fix 2: Bounded Domain Expansion pro Non-Domain Queries

**Soubor:** `runtime/acquisition_strategy.py:3193-3207`

```python
# P0-1 FIX: Omez domain expansion pro non-domain queries
# Non-domain query = bez IOC indikátoru = "free text search"
# Nemá smysl plánovat CT/DOH/WAYBACK lanes pokud nemáme reálné domain seeds

if not has_domain:
    # Pro pure non-domain query: povol pouze PUBLIC a FEED lanes
    # DOMAIN EXPANZE jen pokud je to opravdu threat query s explicit IOC
    if _has_threat_indicator(query) and _has_crypto_indicator(query):
        # "LockBit ransomware" = threat + crypto = má smysl expandovat
        _keyword_expansion = _expand_query_keywords(query)
        if _keyword_expansion:
            _feed_domain_candidates = _keyword_expansion[:5]
            has_domain = True
    else:
        # Pure non-domain query: žádná domain expansion
        _keyword_expansion = ()
        _feed_domain_candidates = ()
```

### Fix 3: DuckDB Init V Parallel S Governor/Plan

**Soubor:** `core/__main__.py:1643-1661` — již je v parallel, ALE `store.async_initialize()` může trvat 30s pokud subprocess init je pomalý.

```python
# P0-1 FIX: DuckDB init má 10s timeout, pak pokračujeme bez něj
try:
    async with asyncio.timeout(10.0):
        results = await asyncio.gather(
            store.async_initialize(),
            _cb_reset_coro,
            return_exceptions=True,
        )
```

**A:** V `_run_internal` — DuckDB inittask běží v parallel s Governor:

```python
_duckdb_init_started = False
if self._duckdb_store is not None:
    _duckdb_init_task = asyncio.create_task(
        self._duckdb_store.async_initialize() if hasattr(self._duckdb_store, 'async_initialize') else asyncio.sleep(0)
    )
    _duckdb_init_started = True
```

### Fix 4: Pipeline pro Non-Domain Query — Rychlá Cesta

**Soubor:** `runtime/acquisition_strategy.py` — nová funkce `_is_fast_nonfeed_query`:

```python
def _is_fast_nonfeed_query(query: str) -> bool:
    """
    P0-1: Non-domain, non-threat, non-crypto query = pure text search.
    Takový query nepotřebuje CT/DOH/WAYBACK lanes a může jet přímo
    přes PUBLIC lane bez elaborate acquisition plan.
    """
    if _has_domain_or_ip(query):
        return False  # Domain/IP = standard path
    if _has_threat_indicator(query):
        return False  # Threat = standard path  
    if _has_crypto_indicator(query):
        return False  # Crypto = standard path
    # "LockBit ransomware" = threat → standard path
    # "APT29" = threat → standard path
    # "best ransomware 2024" = pure text → FAST PATH
    return True
```

Pak v `build_acquisition_plan`:

```python
# P0-1: Fast path pro pure non-domain queries
if _is_fast_nonfeed_query(query):
    # Vrať minimální acquisition plan = pouze PUBLIC lane
    return _build_minimal_plan_for_fast_path(query)
```

## Invarianty (GHOST)

| Invariant | Test |
|-----------|------|
| Prewarm tasks start do 100ms od _run_internal | `test_prewarm_task_timing` |
| DuckDB init timeout 10s, fail-soft | `test_duckdb_init_timeout` |
| Non-domain query: 0 CT/DOH/WAYBACK lanes bez explicitních seeds | `test_fast_nonfeed_path` |
| Prelude total < 30s pro fast-path queries | `test_prelude_timing_fast` |
| Prelude total < 60s pro standard queries | `test_prelude_timing_standard` |

## M1 8GB Bezpečnost

- Prewarm v thread pool = neblokuje event loop = MBTU
- DuckDB timeout 10s = uvolní memory pokud subprocess spawn fail
- Fast path = žádný MLX load pro pure text queries = < 500MB RAM

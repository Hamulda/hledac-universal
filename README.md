# Hledac Universal

Asynchronní autonomní OSINT orchestrátor pro M1 MacBook (8GB UMA).

## Rychlý start

```bash
# Základní sprint
python -m hledac.universal --sprint "incident response target"

# Aggressive mode s plnou rychlostí
python -m hledac.universal --sprint "threat actor infrastructure" --aggressive

# S časovým limitem
python -m hledac.universal --sprint "domain reconnaissance" --duration 300
```

## Architektura

```
┌─────────────────────────────────────────────────────────┐
│                    SprintScheduler                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐ │
│  │ Acquisition │  │  Sidecars   │  │  Brain (MLX)    │ │
│  │   Lanes     │→ │  Advisory   │→ │  DSPy/Hypothesis│ │
│  └─────────────┘  └─────────────┘  └─────────────────┘ │
│         ↓                ↓                ↓              │
│  ┌─────────────────────────────────────────────────────┐│
│  │              DuckDBShadowStore                       ││
│  │   DuckDB (canonical) │ LMDB (metadata) │ LanceDB   ││
│  └─────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────┘
```

## Klíčové funkce

- **CT Intel**: Certstream, Passive DNS, Shodan, Censys, GreyNoise
- **Discovery**: DuckDuckGo, Wayback, CommonCrawl, Providerless cascade
- **Dark surface**: Tor, I2P, IPFS, DHT pivots
- **Enrichment**: BGP, banner grab, identity stitching, leak detection
- **Brain**: Hermes3 MLX inference, DSPy compiled programs

## Feature Flags

| Flag | Default | Popis |
|------|---------|-------|
| `HLEDAC_ENABLE_DSPY=1` | OFF | DSPy hypothesis generation |
| `HLEDAC_ENABLE_HERMES_SYNTHESIS=1` | OFF | Hermes3 synthesis |
| `HLEDAC_ENABLE_BGP=1` | OFF | BGP enrichment |
| `HLEDAC_ENABLE_IPFS=1` | OFF | IPFS discovery |
| `HLEDAC_ENABLE_DARK_PIVOTS=1` | OFF | Tor/I2P pivots |
| `HLEDAC_ENABLE_SHODAN=1` | OFF | Shodan API |

Viz `CLAUDE.md` pro kompletní seznam 45+ feature flags.

## Testování

```bash
# Rychlý test suite
pytest tests/ -x --timeout=30 -q

# Smoke test
python smoke_runner.py --smoke

# Probe testy (sprint-specific)
pytest probe_f226a_mission_runtime/ -v
```

## M1 8GB Memory Budget

| Komponenta | Limit |
|------------|-------|
| macOS | ~2.5 GB |
| Orchestrátor | ~1 GB |
| LLM (Hermes3 4bit) | ~2 GB |
| KV cache | ~0.75 GB |
| **Maximum** | **6.25 GB** |

Metal cache: 2.5 GiB hard cap (`mx.metal.set_cache_limit`)

## Invarianty (GHOST_INVARIANTS.md)

- `asyncio.gather` vždy s `return_exceptions=True`
- `mx.eval([])` před `mx.metal.clear_cache()`
- Žádné `time.sleep()` v async kódu
- DuckDB write pouze přes `async_ingest_findings_batch()`
- LMDB bulk write přes `cursor.putmulti()`

## Struktura projektu

| Adresář | Účel |
|---------|------|
| `runtime/` | Sprint lifecycle, schedulers |
| `knowledge/` | DuckDB, LMDB, LanceDB stores |
| `brain/` | MLX inference, DSPy, hypothesis |
| `fetching/` | HTTP fetching, curl_cffi |
| `transport/` | Tor, I2P, stealth adapters |
| `coordinators/` | Fetch, sidecar orchestration |
| `utils/` | MLX cache, rate limiters, async helpers |
| `tests/` | Unit a integration testy |

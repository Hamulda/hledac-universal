# CLAUDE.md — Hledac Universal OSINT Orchestrator

## PROJECT OVERVIEW

Hledac Universal je asynchronní autonomní OSINT orchestrátor běžící na MacBook Air M1 (8GB UMA).
Používá MLX framework (Metal backend, lazy evaluation) pro LLM inference s Hermes-3-Llama-3.2-3B-4bit modelem.
Orchestrátor běží v tzv. "sprint" cyklech — každý sprint zpracovává vyhledávací dotaz a vrací strukturovaná IoC data.

**Klíčové moduly:**
- `runtime/sprint_scheduler.py` — Sprint lifecycle, koordinace sidecarů, výsledky
- `knowledge/duckdb_store.py` — DuckDB shadow store pro persistentní ukládání CanonicalFinding
- `fetching/public_fetcher.py` — curl_cffi-based HTTP fetching s JA3 fingerprinting
- `knowledge/graph_service.py` — DuckPGQGraph pro entity graph persistence
- `brain/` — MLX inference, DSPy optimizer, hypothesis engine
- `transport/` — Tor, I2P, stealth transport adaptéry
- `coordinators/` — FetchCoordinator, SidecarOrchestrator

**Entry point:** `python -m hledac.universal --sprint "QUERY" [--duration SECS] [--aggressive]`

---

## CRITICAL INVARIANTS (Top 10)

1. **`asyncio.gather` vždy s `return_exceptions=True`** — `_check_gathered()` po každém gather volání
2. **`mx.eval([])` před `mx.metal.clear_cache()`** — jinak clear_cache je no-op
3. **Žádné `time.sleep()` v async kódu** — používat `asyncio.sleep()` nebo `await asyncio.to_thread()`
4. **Žádné `asyncio.run()` v ThreadPoolExecutor** — M1 crash vector, používat `loop.run_until_complete()`
5. **DuckDB write přes `async_ingest_findings_batch()`** — jediná canonical write path, nikdy ne přímo
6. **LMDB bulk write přes `cursor.putmulti()`** — nikdy ne per-item `env.begin(write=True)` v loopu
7. **RotatingBloomFilter pro URL dedup** — nikdy `Set[str]` nebo `ScalableBloomFilter`
8. **M1 Metal cache limit 2.5 GiB** — `mx.metal.set_cache_limit(2_684_354_560)` v `init_mlx_buffers()`
9. **Fail-safe everywhere** — sidecary vrací `[]` při chybách, nikdy nehazují exceptions
10. **Žádné bare `except:`** — vždy `except Exception:` nebo konkrétní typ

---

## CURRENT ARCHITECTURE (Po F260)

### Sprint Pipeline Flow
```
CLI / __main__.py
    └── run_sprint()
            ├── SprintScheduler.run()
            │       ├── run_prelude() — metrics init
            │       ├── run_acquisition_lanes() — CT, public, passive DNS, etc.
            │       ├── run_advisory_runner() — sidecary (IPFS, BGP, dark pivots)
            │       ├── _accumulate_findings_to_graph() — entity upsert
            │       ├── run_winddown() — export, cleanup
            │       └── SprintSchedulerResult
            │
            └── DuckDBShadowStore.async_ingest_findings_batch() — canonical write
                    ├── LMDB metadata (putmulti)
                    └── DuckDB canonical records
```

### Storage Trinity
| Layer | Tech | Purpose |
|-------|------|---------|
| DuckDB | SQL | Canonical findings, queryable |
| LMDB | Key-value | Entity metadata, claim metadata |
| LanceDB | ANN | RAG embeddings |

### Brain Layer (MLX/Hermes3)
- `brain/inference_engine.py` — Hermes3 MLX inference (lazy)
- `brain/dspy_optimizer.py` — DSPy compiled programs (HLEDAC_ENABLE_DSPY=1)
- `brain/hypothesis_engine.py` — Pivot planner, dark surface queries

---

## FEATURE FLAGS (Kompletní seznam)

| Flag | Default | Popis |
|------|---------|-------|
| HLEDAC_ENABLE_ACADEMIC | 0 | Academic research lane (R9) |
| HLEDAC_ENABLE_ALT_PROTOCOLS | 0 | Gopher, Finger, etc. |
| HLEDAC_ENABLE_BANNER_GRAB | 0 | TCP banner enumeration |
| HLEDAC_ENABLE_BGP | 0 | BGP enrichment sidecar (F234) |
| HLEDAC_ENABLE_BGP_PDNS | 0 | Passive DNS via BGP |
| HLEDAC_ENABLE_CAPTCHA_DETECTION | 0 | CAPTCHA solving |
| HLEDAC_ENABLE_CENSYS | 0 | Censys intelligence API |
| HLEDAC_ENABLE_COMMONCRAWL | 0 | CommonCrawl search |
| HLEDAC_ENABLE_CONTENT_LAYER | 0 | Content analysis layer |
| HLEDAC_ENABLE_CURL_CFFI | 0 | curl_cffi HTTP (default: aiohttp) |
| HLEDAC_ENABLE_DARK_PIVOTS | 0 | Tor/I2P/IPFS pivot queries |
| HLEDAC_ENABLE_DHT | 0 | DHT discovery (real UDP) |
| HLEDAC_ENABLE_DIGITAL_GHOST | 0 | Digital forensics steganography |
| HLEDAC_ENABLE_DSPY | 0 | DSPy compiled hypothesis generation |
| HLEDAC_ENABLE_FEDIVERSE | 0 | Fediverse/Mastodon discovery |
| HLEDAC_ENABLE_GOPHER | 0 | Gopher protocol support |
| HLEDAC_ENABLE_GRAPH_ANALYSIS | 0 | Graph analytics |
| HLEDAC_ENABLE_GRAPH_RAG | 0 | Graph RAG embeddings |
| HLEDAC_ENABLE_GREYNOISE | 0 | GreyNoise intelligence API |
| HLEDAC_ENABLE_HEAVY_BROWSER | 0 | Playwright (M1 RAM intensive) |
| HLEDAC_ENABLE_HERMES_SYNTHESIS | 0 | Hermes3 synthesis lane |
| HLEDAC_ENABLE_HTTPX_H2 | 0 | HTTPX HTTP/2 support |
| HLEDAC_ENABLE_HYPOTHESIS | 0 | Hypothesis-driven pivot planner |
| HLEDAC_ENABLE_I2P | 0 | I2P transport |
| HLEDAC_ENABLE_IMAGE_OSINT | 0 | Image forensics |
| HLEDAC_ENABLE_IPFS | 0 | IPFS discovery sidecar |
| HLEDAC_ENABLE_LAYERS | 0 | Security layer manager |
| HLEDAC_ENABLE_LEAKSENTINEL | 0 | Secret/leak detection |
| HLEDAC_ENABLE_LLM | 0 | LLM inference |
| HLEDAC_ENABLE_NODRIVER | 0 | Headless browser (Chrome required) |
| HLEDAC_ENABLE_NYM | 0 | Nym mixnet transport |
| HLEDAC_ENABLE_PRIVACY_LAYER | 0 | Privacy policy enforcement |
| HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY | 0 | Cascade: DDG→Historical→Wayback |
| HLEDAC_ENABLE_RESEARCH_LAYER | 0 | Research analysis layer |
| HLEDAC_ENABLE_SHODAN | 0 | Shodan intelligence API |
| HLEDAC_ENABLE_SOCIAL | 0 | Social media discovery |
| HLEDAC_ENABLE_STEALTH_LAYER | 0 | Stealth mode |
| HLEDAC_ENABLE_STEGANOGRAPHY | 0 | Image steganography detection |
| HLEDAC_ENABLE_SYNTHESIS | 0 | Hermes synthesis (deprecated, use HERMES_SYNTHESIS) |
| HLEDAC_ENABLE_TEMPORAL_STORE | 0 | Temporal data store |
| HLEDAC_ENABLE_TI_FEEDS | 0 | Threat intelligence feeds |
| HLEDAC_ENABLE_TOR | 0 | Tor transport |
| HLEDAC_ENABLE_ZERO_ATTRIBUTION | 0 | Zero-attribution mode |
| HLEDAC_ENABLE_ZKP | 0 | Zero-knowledge proofs |

---

## WIRED COMPONENTS (vs Stub)

| Komponenta | Status | Entry Point |
|------------|--------|-------------|
| SynthesisRunner | WIRED | `sprint_scheduler.py:6335` `_run_synthesis_sidecar()` |
| Hermes3Engine | WIRED | `pipeline/live_public_pipeline.py:2586` |
| DuckPGQGraph | WIRED | `knowledge/graph_service.py` |
| DuckDBShadowStore | WIRED | `knowledge/duckdb_store.py` |
| FetchCoordinator | WIRED | `coordinators/fetch_coordinator.py` |
| IPFS sidecar | WIRED | `sidecar_orchestrator._run_ipfs_discovery_sidecar()` |
| BGP sidecar | WIRED | `sidecar_orchestrator._run_bgp_enrichment_sidecar()` |
| Dark pivots | WIRED | `hypothesis_engine.generate_dark_surface_queries()` |
| Identity stitching | WIRED | `identity_stitching_canonical adapter` |
| Asset exposure | WIRED | `ExposureCorrelatorAdapter` |
| Leak sentinel | WIRED | `LeakSentinelAdapter` |
| Temporal archaeology | WIRED | `TimelineSynthesizer` |
| Quantum pathfinder | READ-SIDE OVERLAY | `DuckPGQGraph.find_connected()` |
| M1ResourceGovernor | WIRED | `core/resource_governor.py` |

---

## DO NOT (Anti-patterns pro agenty)

- **Nepřidávej top-level MLX importy** — MLX se importuje lazy, early import crashuje M1
- **Nepoužívej `time.sleep()` v async kódu** — crash vector, použij `asyncio.sleep()` nebo `await asyncio.to_thread()`
- **Nepiš do DuckDB bez `async_ingest_findings_batch()`** — jediná canonical write path
- **Nepoužívej `asyncio.run()` v ThreadPoolExecutor** — M1 crash, použij `loop.run_until_complete()`
- **Neobcházej `mx.eval([])` před `clear_cache()`** — clear_cache je no-op bez barrier
- **Nepoužívej `ScalableBloomFilter`** — roste bez limitu, nahrazeno `RotatingBloomFilter`
- **Nepoužívej `bytes()` na LMDB buffer** — ničí zero-copy přenos
- **Nikdy nepřidávej `--disable-gpu` do nodriver args** — na M1 je GPU=CPU, zpomalí to
- **Nepvolávej `aggressive_cleanup` bez `()`** — musí být `await self.orch.memory_mgr.aggressive_cleanup()`

---

## TEST BASELINE

| Test Suite | Location | Count | Status |
|------------|----------|-------|--------|
| sprint_scheduler | `tests/test_sprint_scheduler.py` | ~89 | PASS |
| rust extensions | `tests/test_hledac_rust_extensions.py` | ~64 | PASS |
| F206 probe | `probe_f206*` dirs | 200+ | PASS |
| smoke tests | `smoke_runner.py` | — | RUN before PR |

**Před každým PR spusť:**
```bash
pytest tests/ -x --timeout=30 -q
smoke_runner.py --smoke
```

---

## HARDWARE CONSTRAINTS (M1 8GB UMA)

- **RAM budget:** macOS ~2.5GB + orchestrátor ~1GB + LLM ~2GB + KV cache ~0.75GB = **6.25GB max**
- **Metal cache limit:** 2.5 GiB (2_684_354_560 bytes)
- **KV cache:** `kv_bits=4`, `max_kv_size=8192` v `mlx_lm.generate()`, NE v `load()`
- **Soft ceiling:** 5.5 GiB → hard cap fetch concurrency
- **SWAP warning:** `relaxed=False` v MLX je feature, ne bug

---

## KEY SEAMS

| Seam | Canonical Path |
|------|---------------|
| Canonical write | `DuckDBShadowStore.async_ingest_findings_batch()` |
| LMDB metadata | `paths.open_lmdb()` context manager |
| MLX inference | `Hermes3Engine.generate()` |
| HTTP fetch | `FetchCoordinator.fetch()` |
| Graph upsert | `DuckPGQGraph.upsert_ioc()` |

---

*Last updated: F260 (2026-05-30)*

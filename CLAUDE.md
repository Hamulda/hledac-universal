# CLAUDE.md — Hledac Universal OSINT Orchestrator
NEPOUŽÍVEJ ŽÁDNÝ GIT PŘÍKAZ, POKUD O TO NEJSI DOSLOVNĚ ŽÁDÁN!!!
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
8. **M1 Metal cache limit dynamický (MEM-2)** — `min(max(available*0.2, 512MiB), 1GiB)` přes `get_dynamic_metal_cache_limit()` v `_ensure_metal_memory_limits()` — ceiling 1 GiB na M1 8GB; wired limit fixní 1.5 GiB
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

**Pravidlo Q1:** Každý nový `HLEDAC_ENABLE_*` flag MUSÍ mít vyplněný sloupec `Profile`.
Bez `profile` pole flag nebude přijat do CI — viz `tests/probe_q1_arch_rules/`.

| Flag | Default | Profile | Popis |
|------|---------|---------|-------|
| HLEDAC_ENABLE_ACADEMIC | 0 | research | Academic research lane (R9) |
| HLEDAC_ENABLE_ALT_PROTOCOLS | 0 | network | Gopher, Finger, etc. |
| HLEDAC_ENABLE_BANNER_GRAB | 0 | network | TCP banner enumeration |
| HLEDAC_ENABLE_BGP | 0 | intel | BGP enrichment sidecar (F234) |
| HLEDAC_ENABLE_BGP_PDNS | 0 | intel | Passive DNS via BGP |
| HLEDAC_ENABLE_BLOCKCHAIN_ANALYZER | 0 | forensic | Blockchain forensics lane (BTC/ETH address analysis) |
| HLEDAC_ENABLE_CAPTCHA_DETECTION | 0 | browser | CAPTCHA solving |
| HLEDAC_ENABLE_CENSYS | 0 | intel | Censys intelligence API |
| HLEDAC_ENABLE_COMMONCRAWL | 0 | fetch | CommonCrawl search |
| HLEDAC_ENABLE_CONTENT_LAYER | 0 | analysis | Content analysis layer |
| HLEDAC_ENABLE_CURL_CFFI | 0 | fetch | curl_cffi HTTP (default: httpx) |
| HLEDAC_ENABLE_DARK_PIVOTS | 0 | Tor/I2P/IPFS pivot queries |
| HLEDAC_ENABLE_DHT | 0 | DHT discovery (real UDP) |
| HLEDAC_ENABLE_DIGITAL_GHOST | 0 | Digital forensics steganography |
| HLEDAC_DUCKDB_INPROCESS | 1 | DuckDB in-process mode (F275: default ON, saves ~200MB RAM) |
| HLEDAC_DUCKDB_THREADS | 2 | DuckDB thread count (F275: 2 optimal for thread-local conn bottleneck) |
| HLEDAC_ENABLE_DSPY | 0 | DSPy compiled hypothesis generation |
| HLEDAC_ENABLE_FEDIVERSE | 0 | Fediverse/Mastodon discovery |
| HLEDAC_ENABLE_GLINER2 | 1 | MLX GLiNER2 NER (NER engine, M1 RAM budget) |
| HLEDAC_ENABLE_GOPHER | 0 | Gopher protocol support |
| HLEDAC_ENABLE_GRAPH_ANALYSIS | 0 | Graph analytics |
| HLEDAC_ENABLE_GRAPH_RAG | 0 | Graph RAG embeddings |
| HLEDAC_ENABLE_GREYNOISE | 0 | GreyNoise intelligence API |
| HLEDAC_ENABLE_HEAVY_BROWSER | 0 | Playwright (M1 RAM intensive) |
| HLEDAC_ENABLE_HERMES_SYNTHESIS | 0 | Hermes3 synthesis lane |
| HLEDAC_ENABLE_HTTPX_H2 | 0 | HTTPX HTTP/2 support |
| HLEDAC_ENABLE_HTTPX_H3 | 0 | HTTP/3 (QUIC) opportunistic upgrade (curl_cffi `HttpVersion.v3`) + aioquic real-QUIC lane when `[http3]` extra installed. Legacy alias: `HLEDAC_HTTP3=1` |
| HLEDAC_ENABLE_HYPOTHESIS | 0 | Hypothesis-driven pivot planner |
| HLEDAC_ENABLE_I2P | 0 | I2P transport |
| HLEDAC_ENABLE_IMAGE_OSINT | 0 | Image forensics |
| HLEDAC_ENABLE_IPFS | 0 | IPFS discovery sidecar |
| HLEDAC_ENABLE_LAYERS | 0 | Security layer manager |
| HLEDAC_ENABLE_LEAKSENTINEL | 0 | Secret/leak detection |
| HLEDAC_ENABLE_LLM | 0 | LLM inference |
| HLEDAC_ENABLE_MLX_OUTLINES | 1 | MLX outlines NER extractor (NER engine, M1 RAM budget) |
| HLEDAC_ENABLE_NETWORK_RECON | 0 | Network reconnaissance lane (DNS/WHOIS/SSL) |
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
| HLEDAC_LANCEDB_QUANTIZE | 0 | IVF-PQ vector quantization (LanceDB entities + semantic_dedup_v1, M1 8GB friendly, opt-in) |
| HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS | 64 | IVF-PQ num_partitions (LanceDB IVF_PQ index, M1 8GB bounded) |
| HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS | 12 | IVF-PQ num_sub_vectors (12 sub-vectors; 256d/12≈21, 384d/12=32; M1 8GB friendly) |
| HLEDAC_ARROW_INGEST | 1 | Arrow zero-copy ingest for DuckDB (default ON, opt-out=0) |
| HLEDAC_ENABLE_ZKP | 0 | Zero-knowledge proofs |

---

## WIRED COMPONENTS (vs Stub)

| Komponenta | Status | Entry Point |
|------------|--------|-------------|
| SynthesisRunner | WIRED | `runtime/scheduler_v2/acquisition.py:934` `_run_synthesis_sidecar()` |
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
| Threat intel feeds | WIRED | `ThreatIntelSidecarAdapter` (F266-U5) |
| Temporal archaeology | WIRED | `TimelineSynthesizer` |
| Quantum pathfinder | READ-SIDE OVERLAY | `DuckPGQGraph.find_connected()` |
| M1ResourceGovernor | WIRED | `core/resource_governor.py` |

---

## PRE-FLIGHT GUARDS (F221-ABORT)

Hard pre-flight guard in `core/__main__.py::run_sprint` rejects sprints whose
active-window budget would be below `MIN_ACTIVE_WINDOW_S = 30s`. Runs **before**
LMDB / DuckDB init to avoid orphaned lock files on bad config.

- Effective windup replicates F250 (`SprintSchedulerConfig.effective_windup_lead_s`):
  `30% of duration, clamp [30, 180]s`.
- Abort → `sys.exit(2)` = config error, distinguishable from `exit(1)` runtime.
- Override → `--force` flag → `[F221-FORCED]` warning, sprint continues.
- `SprintFlags` frozen dataclass in `core/__main__.py` is the typed contract
  for pre-flight flag bundles; keep it minimal (only flags affecting guards).

**Minimum sprint duration = windup_lead_effective + 30s.**
F250 windup floor = 30s (clamp `[30, 180]`) → use `--duration 60+` to pass the
guard with exactly `MIN_ACTIVE_WINDOW_S=30s` of active window. `--duration 60`
passes (active=30s=MIN, not <). Durations <60s abort with `[F221-ABORT]`
exit code 2 unless `--force` is set. Override with `--force` for explicit
dry-runs where zero evidence is acceptable (emits `[F221-FORCED]` warning).

---

## EXIT CODE CONVENTION (F350M-R)

Top-level `main()` in both `__main__.py` and `core/__main__.py` enforces a
structured exit-code contract. CI/CD pipelines, monitoring, and operator
scripts MUST branch on these values — never treat "exit 0" as the only
green signal.

| Code | Meaning | Trigger |
|------|---------|---------|
| `0`  | Clean success | Sprint completed, artifacts written |
| `1`  | Runtime error (unexpected) | `except Exception` in catch-all envelope |
| `2`  | Config / validation error | F221-ABORT windup guard, `argparse` parse error, flag-conflict |
| `3`  | Programmer error / regression | `NameError`, `AttributeError`, `ImportError` raised deep |
| `130` | `SIGINT` (operator interrupt) | `KeyboardInterrupt` |

**Invariant:** `sys.exit(N)` raised anywhere deep propagates verbatim — the
envelope has `except SystemExit: raise` so deliberate exits are never turned
into generic `exit(1)`.

**Logging:** every fatal exit logs with the `_MAIN_FATAL [exit=N]` prefix via
`_fatal(exc, code)`. Log parsers MUST match this prefix to detect
operator-visible failures.

**Tests:** `tests/test_exit_codes.py` is the regression suite — NameError,
ImportError, KeyboardInterrupt, SystemExit, F221-ABORT guard, and
`--help` clean path are all covered as subprocess tests so the actual
`sys.exit()` code is observable (pytest traps would mask it).

---

## DO NOT (Anti-patterns pro agenty)

- **Nepřidávej top-level MLX importy** — MLX se importuje lazy, early import crashuje M1
- **Nepoužívej `time.sleep()` v async kódu** — crash vector, použij `asyncio.sleep()` nebo `await asyncio.to_thread()`
- **Nepiš do DuckDB bez `async_ingest_findings_batch()`** — jediná canonical write path
- **Nepoužívej `asyncio.run()` v ThreadPoolExecutor** — M1 crash, použij `loop.run_until_complete()`
- **Neobcházej `mx.eval([])` před `clear_cache()`** — clear_cache je no-op bez barrier
- **Nepoužívej `ScalableBloomFilter`** — roste bez limitu, nahrazeno `RotatingBloomFilter`
- **Nepiš raw `try/except ImportError` na module level** — použij `utils.optional_imports.optional()` nebo `core.capabilities.CAP` (777× exists, migrace viz CLAUDE.md sekce "Lazy Import Anti-Pattern")
- **Nepoužívej `bytes()` na LMDB buffer** — ničí zero-copy přenos
- **Nikdy nepřidávej `--disable-gpu` do nodriver args** — na M1 je GPU=CPU, zpomalí to
- **Nepvolávej `aggressive_cleanup` bez `()`** — musí být `await self.orch.memory_mgr.aggressive_cleanup()`

---

## OPTIONAL DEPENDENCIES

| Extra | Install | Purpose |
|-------|---------|---------|
| `mlx-embed` | `uv sync --extra mlx-embed` | MLX-native embedding (M1 unified memory) |
| `http3` | `uv sync --extra http3` | Real QUIC lane via aioquic (stealth/DA+ profiles) |

**mlx-embeddings** (`mlx-embed` extra):
- Provides `mlx-embedding-models>=0.0.1` for Apple Silicon ANE/CoreML embeddings
- Installed automatically on M1 with `uv sync --extra mlx-embed`
- Without it: falls back to transformers-based embedder (slower, more RAM)
- `brain.ane_embedder` auto-detects availability and falls back gracefully

**http3** (`http3` extra):
- Provides `aioquic>=1.3.0` for real QUIC/H3 support
- M1 8GB: ~50-80 MB resident (cryptography + OpenSSL)
- Default install does NOT include it — opt-in via `uv sync --extra http3`
- Used only for stealth/DA+ profile lanes when real QUIC is required

---

## Lazy Import Anti-Pattern (Issue #3)

### Current State
- **777× `except ImportError`** across **361 files**
- Infrastructure EXISTS but underutilized:
  - `core/capabilities.py` — CapabilityRegistry (only 3 files use it!)
  - `utils/optional_imports.py` — `optional()` pattern (0 files use it!)
  - `utils/lazy_singleton.py` — LazySingleton/AsyncLazySingleton (15 files)
  - `core/lazy_imports.py` — PEP 810 lazy imports

### PEP 562 `__getattr__` Already Working
- `brain/__init__.py` — 12 engines lazy-loaded
- `core/__init__.py` — PEP 810
- `intelligence/__init__.py` — PEP 810
- `runtime/__init__.py` — PEP 810

### Migration Pattern

**NEVER** raw `try/except ImportError` at module level:

```python
# WRONG (7µs cold-start penalty per file):
try:
    from otel import instrumented as _otel_instrumented
except ImportError:
    from hledac.universal.otel._instrumentation import instrumented as _otel_instrumented

# RIGHT (zero-cost until first use):
from hledac.universal.utils.optional_imports import optional
_otel_instrumented = optional("otel:instrumented",
    default=optional("hledac.universal.otel._instrumentation:instrumented"))
```

**ALLOWED:** `except ImportError` inside methods — legitimate runtime deferral.

### Top Migration Targets
1. `__main__.py` — 4× module-level deferred
2. `utils/platform_info.py` — 6× module-level deferred  
3. `tools/url_dedup.py` — 7× module-level deferred
4. `knowledge/lancedb_store.py` — 5× module-level deferred

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
- **Metal cache limit:** 1.5 GiB (1_610_612_736 bytes)
- **KV cache:** `kv_bits=4`, `max_kv_size=8192` v `mlx_lm.generate()`, NE v `load()`
- **Soft ceiling:** 5.5 GiB → hard cap fetch concurrency
- **SWAP warning:** `relaxed=False` v MLX je feature, ne bug

---

## HTTP/3 (QUIC) — P1-2 Sprint 2026-06-08

Centralized HTTP/3 lane in `transport/http3_lane.py` (new). Two strategies behind one bounded layer:

| Strategy | Mechanism | Cost | Use case |
|----------|-----------|------|----------|
| `curl_cffi_opportunistic` (default) | `curl_cffi >= 0.7` `HttpVersion.v3` kwarg, gated on Alt-Svc h3 advertisement | 0 extra deps | All clearnet fetches that benefit from Alt-Svc-driven H3 upgrade |
| `aioquic_stealth` (opt-in via `[http3]` extra) | Real QUIC handshake + H3 via `aioquic` | ~50-80 MB resident (cryptography + OpenSSL) | Stealth / DA+ profile lane when real QUIC is required |

**Env gate:** `HLEDAC_ENABLE_HTTPX_H3=1` enables both strategies; default **ON** (always-on, opt-out via `HLEDAC_ENABLE_HTTPX_H3=0`). Legacy alias `HLEDAC_HTTP3=1` (F260) is honored for back-compat.

**M1 8GB bounds** (`transport/http3_lane.py`):
- `_H3_CACHE_MAX = 1024` — bounded LRU (host → `True`), FIFO eviction
- `_H3_CONCURRENCY_MAX = 3` — semaphore caps concurrent aioquic handshakes
- `_H3_TIMEOUT_S = 8.0` — per-request `asyncio.wait_for` hard cap
- `_H3_CACHE_TTL_S = 86_400` — 24h, same as stealth_manager F194
- `_H3_RSS_BLOCK_GIB = 5.5` — psutil probe blocks the lane at mission budget
- `aioquic` lives in `[http3]` extra ONLY (NOT in `m1-local`)

**Fail-soft invariants:** every error path returns `None` and lets the caller continue on HTTP/1.1 / HTTP/2. No bare `except:`; every cache write is best-effort; cooperative `CancelledError` is re-raised.

**Probe tests:** `tests/probe_p12_http3_lane/test_p12_http3_lane.py` — 48 hermetic tests covering: lazy import without aioquic, env gate resolution (incl. legacy alias), Alt-Svc parser, bounded LRU + TTL + LRU touch, per-request timeout, semaphore saturation, memory guard, record helpers, F260 compat shims, transport router H3 candidate, `pyproject.toml` `[http3]` extra presence + `m1-local` exclusion.

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

## IOC Extraction — Dual Engine

Projekt má dva IOC extraktory s různými rolemi:

| Engine | Entry point | Metoda | Kdy použít |
|---|---|---|---|
| Rust regex | `rust.ioc.extract_iocs_flat(text)` | Regex patterns | Clearnet IOC, rychlost, high volume |
| Brain NER | `brain.ner_engine.extract_iocs_from_text(text)` | ML NER | Nestrukturovaný text, nízká precision OK |

NESMÍCHÁVAT: `live_public_pipeline.py` volá oba — to je správně.
Rust = primární pro strukturovaná data (HTML, JSON).
NER = sekundární pro volný text (forum posts, dark web).

---

## F265B — Curl CFFI Prewarm + Conditional Cache (2026-06-10)

Closes two gaps in the curl_cffi stealth lane that F260 + F261 left open:
1. **Prewarm** — eliminates the 200-400 ms TLS handshake cost on the
   first request to a profile.
2. **Conditional cache** — closes the gap that hishel (F261) only
   covered the httpx path; SERP fetches through curl_cffi now enjoy
   ETag/Last-Modified 304 short-circuits.

### Prewarm pool (`transport/prewarm_pool.py`)
- 4-slot ring buffer. Round-robin across slots; on a hit, the other
  slot is re-prewarmed in the background so the pool stays warm.
- Bounded: exactly 4 sessions, never grows.
- M1 8GB: ~60 MB resident for 4 sessions (~15 MB each).
- Fail-soft: any error → lazy runtime path.
- Opt-out: `HLEDAC_CURL_CFFI_PREWARM=0` (default ON).
- Wired into `curl_cffi_runtime._get_or_create_session` — callers
  see no API change.

### Conditional cache (`transport/conditional_cache.py`)
- LMDB-backed (16 MB map, 5000 entries) with zstd/zlib compression.
- In-memory fallback when LMDB is unavailable (hermetic tests).
- Stores `(etag, last_modified, body, sha256, fetched_at, status_code)`.
- 304 = `If-None-Match` / `If-Modified-Since` short-circuit, returns
  cached body, 0 bytes transferred. ~200 ms vs ~3 s.
- TTL: 1 hour (Bing SERP freshness window).
- Bounds: 256 B ≤ body ≤ 2 MB per entry.
- Opt-out: `HLEDAC_CONDITIONAL_CACHE=0` (default ON).
- Public wrapper: `fetch_via_curl_cffi_cached(url, ..., _force_refresh=False)`.
- Wired into the F260 call sites in `public_fetcher.py` (primary +
  403/429 escalation). `probe_altsvc_speculative(url)` is now
  called once per primary fetch to prime the H3 LRU in the
  background (so the SECOND fetch, not the first, can use
  `HttpVersion.v3`).

### Speculative Alt-Svc probe
Added to `transport/http3_lane.py`. Fire-and-forget HEAD that
primes the H3 LRU before the second fetch hits the same host.
Idempotent: skips hosts already in the LRU. Gated by
`HLEDAC_ENABLE_HTTPX_H3=1`. Never raises. No event loop → no-op.

### Probe tests
`tests/probe_p14_prewarm_conditional/` — 25 hermetic tests covering:
prewarm opt-out, fallback, round-robin, bounds, stats, conditional
cache miss/hit/store/headers/304/force-refresh/error path, LMDB and
in-memory backends, TTL expiry, LRU eviction, compression roundtrip,
speculative Alt-Svc gating.

### Invariants
- **Always-on, bounded, fail-safe** — no new feature flags beyond
  opt-out env vars; prewarm disabled → lazy fallback; conditional
  cache disabled → live fetch; LMDB missing → in-memory fallback;
  any error → no exception, telemetry records the miss.
- **No new public APIs required** — `fetch_via_curl_cffi_cached`
  drops in at the existing call sites; `probe_altsvc_speculative`
  is fire-and-forget.
- **M1 8GB safe** — 60 MB prewarm + 16 MB LMDB map + 1-hour TTL.
- **Lazy imports** — no curl_cffi / aioquic / zstandard at module
  load; all deps loaded on first use.

---

*Last updated: F265B (2026-06-10)*
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.

<!-- rtk-instructions v2 -->
# RTK (Rust Token Killer) - Token-Optimized Commands

## Golden Rule

**Always prefix commands with `rtk`**. If RTK has a dedicated filter, it uses it. If not, it passes through unchanged. This means RTK is always safe to use.

**Important**: Even in command chains with `&&`, use `rtk`:
```bash
# ❌ Wrong
git add . && git commit -m "msg" && git push

# ✅ Correct
rtk git add . && rtk git commit -m "msg" && rtk git push
```

## RTK Commands by Workflow

### Build & Compile (80-90% savings)
```bash
rtk cargo build         # Cargo build output
rtk cargo check         # Cargo check output
rtk cargo clippy        # Clippy warnings grouped by file (80%)
rtk tsc                 # TypeScript errors grouped by file/code (83%)
rtk lint                # ESLint/Biome violations grouped (84%)
rtk prettier --check    # Files needing format only (70%)
rtk next build          # Next.js build with route metrics (87%)
```

### Test (60-99% savings)
```bash
rtk cargo test          # Cargo test failures only (90%)
rtk go test             # Go test failures only (90%)
rtk jest                # Jest failures only (99.5%)
rtk vitest              # Vitest failures only (99.5%)
rtk playwright test     # Playwright failures only (94%)
rtk pytest              # Python test failures only (90%)
rtk rake test           # Ruby test failures only (90%)
rtk rspec               # RSpec test failures only (60%)
rtk test <cmd>          # Generic test wrapper - failures only
```

### Git (59-80% savings)
```bash
rtk git status          # Compact status
rtk git log             # Compact log (works with all git flags)
rtk git diff            # Compact diff (80%)
rtk git show            # Compact show (80%)
rtk git add             # Ultra-compact confirmations (59%)
rtk git commit          # Ultra-compact confirmations (59%)
rtk git push            # Ultra-compact confirmations
rtk git pull            # Ultra-compact confirmations
rtk git branch          # Compact branch list
rtk git fetch           # Compact fetch
rtk git stash           # Compact stash
rtk git worktree        # Compact worktree
```

Note: Git passthrough works for ALL subcommands, even those not explicitly listed.

### GitHub (26-87% savings)
```bash
rtk gh pr view <num>    # Compact PR view (87%)
rtk gh pr checks        # Compact PR checks (79%)
rtk gh run list         # Compact workflow runs (82%)
rtk gh issue list       # Compact issue list (80%)
rtk gh api              # Compact API responses (26%)
```

### JavaScript/TypeScript Tooling (70-90% savings)
```bash
rtk pnpm list           # Compact dependency tree (70%)
rtk pnpm outdated       # Compact outdated packages (80%)
rtk pnpm install        # Compact install output (90%)
rtk npm run <script>    # Compact npm script output
rtk npx <cmd>           # Compact npx command output
rtk prisma              # Prisma without ASCII art (88%)
```

### Files & Search (60-75% savings)
```bash
rtk ls <path>           # Tree format, compact (65%)
rtk read <file>         # Code reading with filtering (60%)
rtk grep <pattern>      # Search grouped by file (75%). Format flags (-c, -l, -L, -o, -Z) run raw.
rtk find <pattern>      # Find grouped by directory (70%)
```

### Analysis & Debug (70-90% savings)
```bash
rtk err <cmd>           # Filter errors only from any command
rtk log <file>          # Deduplicated logs with counts
rtk json <file>         # JSON structure without values
rtk deps                # Dependency overview
rtk env                 # Environment variables compact
rtk summary <cmd>       # Smart summary of command output
rtk diff                # Ultra-compact diffs
```

### Infrastructure (85% savings)
```bash
rtk docker ps           # Compact container list
rtk docker images       # Compact image list
rtk docker logs <c>     # Deduplicated logs
rtk kubectl get         # Compact resource list
rtk kubectl logs        # Deduplicated pod logs
```

### Network (65-70% savings)
```bash
rtk curl <url>          # Compact HTTP responses (70%)
rtk wget <url>          # Compact download output (65%)
```

### Meta Commands
```bash
rtk gain                # View token savings statistics
rtk gain --history      # View command history with savings
rtk discover            # Analyze Claude Code sessions for missed RTK usage
rtk proxy <cmd>         # Run command without filtering (for debugging)
rtk init                # Add RTK instructions to CLAUDE.md
rtk init --global       # Add RTK to ~/.claude/CLAUDE.md
```

## Token Savings Overview

| Category | Commands | Typical Savings |
|----------|----------|-----------------|
| Tests | vitest, playwright, cargo test | 90-99% |
| Build | next, tsc, lint, prettier | 70-87% |
| Git | status, log, diff, add, commit | 59-80% |
| GitHub | gh pr, gh run, gh issue | 26-87% |
| Package Managers | pnpm, npm, npx | 70-90% |
| Files | ls, read, grep, find | 60-75% |
| Infrastructure | docker, kubectl | 85% |
| Network | curl, wget | 65-70% |

Overall average: **60-90% token reduction** on common development operations.
<!-- /rtk-instructions -->

## Agent skills

### Issue tracker

Local Markdown — issues žijí jako soubory v `.scratch/<feature>/issues/<NN>-<slug>.md`, PRD je `.scratch/<feature>/PRD.md`, stav na řádku `Status:` v hlavičce issue. Viz `docs/agents/issue-tracker.md`.

### Triage labels

Kanonie 1:1 — `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. Viz `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` + `docs/adr/` v rootu, produkované lazy přes `/grill-with-docs`. Viz `docs/agents/domain.md`.

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
<!-- BEGIN BYTEROVER RULES -->

# Workflow Instruction

You are a coding agent integrated with ByteRover via MCP (Model Context Protocol).

## Core Rules

1. **Query First**: Automatically call the mcp tool `brv-query` when you need to query the context for the task and you do not have the context.
2. **Curate Later**: After finishing the task, call `brv-curate` to store back the knowledge if it is very important.

## Tool Usage

- `brv-query`: Query the context tree.
- `brv-curate`: Store context to the context tree.


---
Generated by ByteRover CLI for Claude Code
<!-- END BYTEROVER RULES -->

<!-- crystl-cli:begin v2.144.1 -->
@AGENTS.md
<!-- crystl-cli:end -->

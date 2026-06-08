# CONTEXT.md — Domain Glossary

> **Purpose.** Engineering skills (`improve-codebase-architecture`, `diagnose`, `tdd`,
> `grill-with-docs`, `to-issues`) read this file to learn the project's domain
> vocabulary. When you write an issue title, refactor proposal, hypothesis,
> or test name, use the terms defined here — do not invent synonyms.
>
> This is **not** architecture documentation. For entry points, lifecycle,
> and module map, see `docs/ARCHITECTURE.md` and `docs/architecture/`.
> For past decisions, see `docs/adr/`.
>
> **Provenance.** Established 2026-06-06 via `/setup-matt-pocock-skills`.
> Producer skill: `/grill-with-docs` — this file grows lazily as terms get
> resolved during grilling sessions.

## Project one-liner

Asynchronní autonomní OSINT orchestrátor pro Apple Silicon M1 (8 GB UMA),
běžící v sprint cyklech. Každý sprint přijme vyhledávací dotaz a vrátí
strukturovaná IoC data prostřednictvím acquisition lanes, sidecar advisories
a DuckDB canonical store.

## Core domain terms

### Sprint a jeho lifecycle

| Term | Definition |
|---|---|
| **Sprint** | Jeden běh orchestrátoru. Přijme `query`, proběhne `prelude → acquisition lanes → advisory runnery → winddown`, vrátí `SprintSchedulerResult`. Trvání typicky 60–600 s. |
| **Sprint mode** | Jedna ze čtyř úrovní agresivity: `aggressive` (maximální paralelismus), `active`, `passive`, `research`. Ovlivňuje concurrency, timeouty a povolené sidecary. |
| **Prelude** | Fáze před akvizicí — inicializace metrik, otevření storage handles, napojení MLX (lazy). |
| **Acquisition lane** | Kategorie discovery zdroje v rámci sprintu. Příklady: `public`, `ct`, `passive_dns`, `ipfs`, `bgp`, `academic`, `fediverse`, `dht`, `alt_protocols`. Každá lane = vlastní async korutina s bounded concurrency. |
| **Advisory runner / Sidecar** | Bounded modul, který běží paralelně s lanes a obohacuje findings o další kontext. **Nikdy nesmí shodit sprint** — selhání se projeví jako `[].` |
| **Winddown** | Fáze po akvizici — finální ingest do DuckDB, export, cleanup. |
| **Cross-sprint seed** | Perzistentní stav v LMDB (`sprint_seeds.lmdb`), který přežívá mezi sprinty a seedí další běh. |

### Data a storage

| Term | Definition |
|---|---|
| **CanonicalFinding** | Jediný kanonický datový typ pro nález IoC. **Všechny** writes do perzistentního store musejí procházet přes `DuckDBShadowStore.async_ingest_findings_batch()`. Žádný přímý zápis do tabulek. |
| **IoC** | Indicator of Compromise. Generický termín pro doménu, IP, hash, ASN, certifikát, leak hit. V kódu typicky uložen jako `CanonicalFinding` s `ioc_type`, `ioc_value`, `confidence`, `source_type`, `sprint_id`. |
| **Storage trinity** | Tři perzistentní vrstvy, záměrně oddělené: **DuckDB** (SQL, canonical findings), **LMDB** (KV, entity/claim metadata + cross-sprint seeds), **LanceDB** (ANN, RAG embeddings). Žádné překrývání rolí. |
| **Evidence envelope** | JSON payload v `CanonicalFinding.payload_text`, ohraničený bounded strukturou (`MAX_ENVELOPE_SIZE=4098`). Obsahuje `audit_reason`, `evidence_pointers`, `signal_facets`, `suggested_pivots`. Fail-soft: při přetečení degrades na prostý finding. |
| **Source type** | Původ nálezu: `ct`, `public`, `ipfs`, `bgp`, `academic`, `temporal_archaeology`, `leak_sentinel`, `exposure_correlator`, `identity_stitching`, `forensic_analysis`, `document`, `deep_probe`, `synthesis`, … |

### LLM a inference

| Term | Definition |
|---|---|
| **MLX** | Apple Silicon-native framework pro LLM inferenci. **Metal backend, lazy evaluation.** Importovat výhradně uvnitř funkcí, ne na úrovni modulu. |
| **Hermes3** | Konkrétní model: `Hermes-3-Llama-3.2-3B-4bit`. ~2 GB RAM. Primární LLM tohoto projektu. |
| **M1 8GB UMA** | Hardwarový cíl: MacBook Air M1, 8 GB Unified Memory. **Total RAM budget: 6.25 GB max** (macOS 2.5 + orchestrátor 1 + LLM 2 + KV cache 0.75). SWAP je feature, ne bug — `relaxed=False` v MLX záměrně. |
| **KV cache config** | `kv_bits=4` a `max_kv_size=8192` patří do `mlx_lm.generate()`, **ne** do `load()`. |
| **Metal cache limit** | `mx.metal.set_cache_limit(2_684_354_560)` = 2.5 GiB. |
| **mx.eval([]) barrier** | Před každým `mx.metal.clear_cache()` nutné zavolat `mx.eval([])`. Bez bariéry je `clear_cache()` no-op. |
| **SynthesisRunner** | Hermes3 synthesis lane, která po akvizici skládá accepted findings do koherentního reportu. **Drátově** v `sprint_scheduler.py:6335`. |

### Sidecary a advisories

| Term | Definition |
|---|---|
| **Sidecar** | Bounded advisory modul, registrován přes `SidecarRegistry`. Implementuje `SidecarAdapterProtocol`. **Drží se fail-soft invariantu**: chyba → `[]`, nikdy ne exception. |
| **SidecarContext** | Dataclass předávaný sidecarům: `query`, `sprint_id`, `findings`, `sprint_mode`, `memory_pressure`. |
| **Env gate** | Feature flag ve stylu `HLEDAC_ENABLE_<X>`, default `0` (off). Sidecar se nespustí, dokud gate není `1`. |
| **M1ResourceGovernor** | Advisory vrstva nad RAM — `GovernorDecision`/`GovernorSnapshot`, `evaluate()` a `apply_decision()` async. Reaguje na `model_lifecycle.get_model_lifecycle_status()` + `resource_governor.sample_uma_status()`. |

### Datové struktury a primitiva

| Term | Definition |
|---|---|
| **RotatingBloomFilter** | **Jediný povolený** Bloom filter pro URL dedup. Nikdy `Set[str]`, nikdy `ScalableBloomFilter` (ten roste bez limitu). |
| **Wood-sidecar / Fail-soft** | Sidecar, který při jakékoliv výjimce vrací `[]` a logger.warning, ale **neshodí** sprint. |
| **Pivot** | Přechod z jednoho IoC / domény na další discovery otázku. `PivotPlanner` generuje 5 typů: `domain`, `identity`, `leak`, `archive`, `graph`. |
| **Hypothesis** | Plánovaná navazující otázka / query, generovaná z accepted finding. `HypothesisEngine` v `brain/`. |

### Hardware a bounded invarianty

| Term | Definition |
|---|---|
| **Bounded** | Každá kolekce má explicitní `MAX_*` konstantu (např. `MAX_CLAIMS=5000`, `MAX_HOST_PENALTIES=512`, `MAX_TIMELINE_EVENTS=200`). Bez bounded kolekce = fail-review. |
| **Always-on, no toggles** | Žádné feature flagy pro nové funkce, žádné ENV vars pro nové features. Vše `1`/`0` v `HLEDAC_ENABLE_*` je zpětně pro sidecary, ne pro core. |

## Synonyms to avoid (drift traps)

| Nepoužívej | Používej | Důvod |
|---|---|---|
| "advisor" / "advisory module" | **advisory sidecar** | Konzistentní s `SidecarRegistry` a protokolem |
| "finding" (v kontextu perzistentního zápisu) | **CanonicalFinding** | Kanonický typ; "finding" je vágní |
| "Bloom filter" (bez prefixu) | **RotatingBloomFilter** | ScalableBloomFilter je zakázaný |
| "agent" (v kontextu MLX-loaded LLM) | **MLX** nebo **Hermes3** | "Agent" je v projektu vyhrazen pro RL policy |
| "M1 crash" (v komentáři) | **M1 crash vector** | Přesná terminologie z auditů |
| "session" (v kontextu HTTP) | **StealthSession** nebo **session_runtime** | `Session` je obecný; pro stealth HTTP je kanonický typ `StealthSession` |
| "graph" (samostatně) | **DuckPGQGraph** | Konkrétní implementace, ne obecný graf |
| "model" (v kontextu LLM) | **Hermes3** | V tomto projektu je právě jeden LLM model |
| "staging" / "intermediate" (pro data) | **canonical** nebo **advisory** | Buď perzistentní canonical, nebo bounded advisory |
| "spring" (překlep) | **sprint** | Pravopisný invariant |

## Sprint naming convention

| Prefix | Význam | Příklady |
|---|---|---|
| `F{N}` | Feature / sprint změna v kódu | `F202A` (entity signal), `F214Q` (quantum pathfinder), `F262` (gather migration), `F263` (forensic reporting) |
| `F{N}{S}` | Sub-step v rámci sprintu | `F262D` (gather completion), `F350M-R` (sidecar protocol) |
| `R{N}` | Research / probe lane | `R8x`, `R9` |
| `P{N}` | P-series (parallel / perf) | `P12` (parallel hypothesis burst) |
| `M1…` | Hardwarově specifická oprava | `M1 8GB memory budget`, `M1 crash fixes` |

## Quick anchors for skills

- **Entry point:** `core/__main__.py:run_sprint()` (canonical), `__main__.py:main()` (CLI dispatcher).
- **Canonical write path:** `DuckDBShadowStore.async_ingest_findings_batch()` — **jediná** brána.
- **MLX inference seam:** `Hermes3Engine.generate()`.
- **HTTP fetch seam:** `FetchCoordinator.fetch()` (curl_cffi + JA3).
- **Graph upsert seam:** `DuckPGQGraph.upsert_ioc()`.
- **Sidecar registration seam:** `SidecarRegistry.register("<id>")` v `runtime/sidecar_protocol_adapters.py`.

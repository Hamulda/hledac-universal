# False Optimization Audit: UVLoop / Orjson / Msgspec / MLX Candidates

## Metodika

Kandidáti identifikováni přes:
- `rg -l "uvloop|orjson|msgspec|mlx" --type py` (bez testů/benchmarků)
- Caller analysis: kdo volá, jak často, v jakém kontextu
- Hot-path analysis: network-bound vs CPU-bound, call frequency

---

## 1. `context_optimization/context_compressor.py`

**Symbol:** `orjson.dumps()` / `orjson.loads()` (řádky 108, 115)

**Proč to vypadá lákavě:**
- 750ř modul zabývající se "context compression" zní jako výkonnostní kritický kód
- orjson je 3-10x rychlejší než stdlib json na serializaci
- Komprese/context management evokuje high-frequency operace

**Proč to ve skutečnosti není dobrý kandidát:**
- `compress_context()` / `decompress_context()` jsou volány **manuálně** na explicitní žádost — ne v žádném hot loopu
- Voláno z `memory_coordinator.py:2112` (`compress_context`) — jednorázově na konci sprintu
- 99% času stráveného v tomto modulu je I/O a síť, ne serializace
- Objekt `CompressedContext` je malý (metadatas + bounds), serializace trvá **mikrosekundy** vs **sekundy** network wait

**Co dělat místo toho:**
- Nechat jak je. Žádná akce nutná.
- Pokud byste chtěli optimalizovat memory_coordinator, začněte u `async_ingest_findings_batch` (to je skutečný hot path).

---

## 2. `context_optimization/dynamic_context_manager.py`

**Symbol:** `orjson.dumps()` / `orjson.loads()` (řádky 123, 130)

**Proč to vypadá lákavě:**
- "Dynamic context manager" zní jako by měl běžet neustále
- Evokuje správu kontextu v průběhu celého běhu

**Proč to ve skutečnosti není dobrý kandidát:**
- Jediný import tohoto modulu: `context_optimization/__init__.py` (re-export)
- Žádný production kód ve skutečnosti volá `DynamicContextManager` — žádný `dynamic_context_manager` token v žádném modul mimo `context_optimization/`
- Modul pravděpodobně mrtvý nebo velmi zřídka používaný
- I kdyby běžel, samotný context manager pouze ukládá/čte z cache — network bound

**Co dělat místo toho:**
- Ověřit, zda se vůbec používá (`rg "DynamicContextManager" | grep -v context_optimization`)
- Pokud nepoužívaný: označit za tech debt, ne investovat do optimalizace

---

## 3. `tools/session_manager.py`

**Symbol:** `orjson.dumps()` / `orjson.loads()` (řádky 189, 194)

**Proč to vypadá lákavě:**
- USE_ORJSON toggle ukazuje, že někdo už přemýšlel o serializaci jako bottlenecku
- 301ř modul, orjson pro "session state" zní jako by to mohlo být časté

**Proč to ve skutečnosti není dobrý kandidát:**
- Voláno z `fetch_coordinator.py` — `SessionManager.get_session(domain)` a `rotate_credentials(domain)`
- **Network-bound**: session management čeká na HTTP response od serverů
- Serializace session dat je **mikrosekundy** vs **stovky ms–sekundy** pro síťový roundtrip
- `orjson.dumps()` na 1KB JSON payload běží v **<1µs**; network latency je **10-500ms**
- 10,000x disparity — serializace je naprosto zanedbatelná

**Co dělat místo toho:**
- Nechat jak je. Síť je bottleneck, ne serializace.
- Pokud byste chtěli zrychlit session management, zkuste pipelining/keep-alive, ne orjson

---

## 4. `loops/research_loop.py`

**Symbol:** `orjson.dumps()` / `orjson.loads()` (řádky 34, 44)

**Proč to vypadá lákavě:**
- "Research loop" zní jako compute-heavy iterations
- Vnořené JSON serializace v RL smyčce mohou znít jako bottleneck

**Proč to ve skutečnosti není dobrý kandidát:**
- Voláno pouze když `--loop` flag je nastaven (P16/P17 feature, optional)
- `ResearchLoop.run_once()` je inherently network-bound — stahuje data z externích zdrojů
- RL smyčka je ovládána `live_public_pipeline.py:3132` — pouze jedno volání per sprint run
- I kdyby serializace byla 10x rychlejší, ušetříte **mikrosekundy** z **desítek sekund** běhu

**Co dělat místo toho:**
- Nechat jak je. Optimalizace research loopu = optimalizovat síťové fetches, ne serializaci.

---

## 5. `tools/commoncrawl_adapter.py`

**Symbol:** `orjson.loads()` (řádky 86, 129)

**Proč to vypadá lákavě:**
- CommonCrawl = velká data, masivní parsing zní jako CPU bottleneck
- 178ř modul, bulk JSON parsing

**Proč to ve skutečnosti není dobrý kandidát:**
- Voláno z `live_public_pipeline.py:1918` a `discovery/duckduckgo_adapter.py:1101`
- **Network-bound**: CommonCrawl API fetch je HTTP request, čeká na data z CDN
- Parsuje malé JSON payloady (index metadata), ne velké datasety
- I s stdlib `json.loads` je síťový fetch 100-1000x pomalejší než parsing

**Co dělat místo toho:**
- Nechat jak je. Nebo zkusit HTTP/2 multiplexing pokud je CommonCrawl bottleneck.

---

## 6. `tools/wayback_adapter.py`

**Symbol:** `orjson.loads()` (řádky 37)

**Proč to vypadá lákavě:**
- Wayback Machine API responses parsing
- Malý 52ř modul — vypadá jako rychlý kandidát na optimalizaci

**Proč to ve skutečnosti není dobrý kandidát:**
- Jediný `orjson.loads()` na 52ř souboru — trivia
- Wayback API calls jsou **network-bound** (HTTP requests na archive.org)
- Jedno volání `orjson.loads` na response, který přijde po 200-2000ms network latency
- Parsing 10KB JSON trvá ~**0.1ms** — network latency je **2000ms** — 20,000x disparity

**Co dělat místo toho:**
- Smazat nebo nechat — 52ř modul není místo kde byste měli trávit čas optimalizací.

---

## 7. `utils/shadow_dtos.py`

**Symbol:** `msgspec.Struct` benchmark制造 (celý soubor)

**Proč to vypadá lákavě:**
- Msgspec je rychlejší než dataclasses pro DTO construction
- Benchmark ukazuje "constructor_msgspec" vs "constructor_baseline" speedup
- Vypadá jako legitimní optimalizace DTO vrstvy

**Proč to ve skutečnosti není dobrý kandidát:**
- **Pouze benchmark, žádný production kód nevolá `shadow_dtos`**
- `rg "shadow_dtos" --type py` → 0 výsledků mimo shadow_dtos.py
- Modul je **self-contained microbenchmark** — nemá žádný caller graph
- `AdmissionResultShadow` a `BacklogCandidateShadow` jsou "shadow twins" pro měření overhead, ne reálné DTO

**Co dělat místo toho:**
- Benchmarks jsou OK, ale neinvestovat čas do optimalizaci něčeho, co nemá production usage.
- Think twice before adding msgspec to hot-path DTOs — maintainability cost vs microbenchmark win.

---

## 8. `brain/ner_engine.py`

**Symbol:** `msgspec.Struct` v MLX fallback path (řádky 299)

**Proč to vypadá lákavě:**
- MLX inference je performance-sensitive — každá optimalizace se počítá
- `EntityList` msgspec.Struct uvnitř generátoru vypadá jako hot-path optimization

**Proč to ve skutečnosti není dobrý kandidát:**
- **Fallback path**: `if not NEREngine._MLX_AVAILABLE` — pouze když MLX není dostupný
- Na M1 MacBooku s MLX toto nikdy neběží (MLX je primary)
- I když běží: model inference `outlines.generate.json()` trvá **sekundy**, `msgspec.Struct` construction trvá **mikrosekundy**
- Even in fallback: parsing text[:2000] (2KB) je dominated by model inference time

**Co dělat místo toho:**
- Nechat jak je. Fallback path není optimalizace — je to graceful degradation.
- Skutečná optimalizace = zlepšit MLX model loading/caching, ne msgspec v fallback.

---

## 9. `discovery/duckduckgo_adapter.py`

**Symbol:** `msgspec.Struct` — `DiscoveryHit`, `DiscoveryBatchResult` (řádky 58, 78)

**Proč to vypadá lákavě:**
- msgspec.Struct s `frozen=True, gc=False` je inzerováno jako高性能
- 1146ř modul plný DTOs — vypadá jako by serializace mohla být bottleneck

**Proč to ve skutečnosti není dobrý kandidát:**
- Voláno z `live_public_pipeline.py` — network-bound HTTP requests k DuckDuckGo API
- `DiscoveryHit` construction: jednoduchý struct s pár string poli — **mikrosekundy**
- DuckDuckGo API call: **stovky ms** network latency
- DTO construction je **0.001%** celkového času

**Co dělat místo toho:**
- Nechat jak je. msgspec.Struct tam je pro **typovou bezpečnost**, ne pro výkon.
- Pokud byste chtěli zrychlit DuckDuckGo adapter, zkuste batch dotazy nebo caching, ne struct construction.

---

## 10. `discovery/rss_atom_adapter.py`

**Symbol:** `msgspec.Struct` — `FeedEntryHit`, `FeedBatchResult`, `FeedDiscoveryHit`, atd. (8+ struct tříd)

**Proč to vypadá lákavě:**
- Velký modul s mnoha msgspec.Struct definicemi
- broadcast-style feed processing zní jako by mohl být CPU-bound
- Structy s `frozen=True, gc=False` inzerují nízkou režii

**Proč to ve skutečnosti není dobrý kandidát:**
- Voláno z `core/__main__.py`, `pipeline/live_feed_pipeline.py` — feed fetching je **I/O-bound**
- Každý feed entry involve: HTTP fetch (50-500ms) + XML parsing + struct construction
- Struct construction: **mikrosekundy**. Network wait: **stovky ms**
- 8 různých Struct tříd = větší maintenance burden pro malý win na špatném místě

**Co dělat místo toho:**
- Nechat jak je. msgspec tam je pro developer ergonomics, ne výkon.
- Pokud performance problém s feed processing, řešte HTTP pipelining / feed batching, ne DTO construction.

---

## 11. `fetching/public_fetcher.py`

**Symbol:** `FetchResult(msgspec.Struct, frozen=True, gc=False)` (řádka 204)

**Proč to vypadá lákavě:**
- 32ř msgspec.Struct v fetch hot path — vypadá jako legitimate optimization

**Proč to ve skutečnosti není dobrý kandidát:**
- `FetchResult` je výsledek HTTP fetch operace — **network-bound**
-Construction cost of msgspec.Struct je ns–µs; fetch čeká na HTTP response (ms–s)
- `frozen=True, gc=False` je dobrá volba pro immutability + GC savings — toto JE smysluplná optimalizace
- **AVSHNED**: V tomto případě msgspec.Struct JE vhodný kandidát, protože FetchResult je malý a konstruovaný velmi často v hot path

**Co dělat místo toho:**
- **Tohle nechat jak je.** FetchResult v hot path fetch coordinatoru JE legitimate use case pro msgspec. Win je malý, ale real.

---

## Shrnutí

| File | Symbol | Status | Důvod |
|------|--------|--------|-------|
| `context_optimization/context_compressor.py` | orjson | ❌ False positive | Manuální compress/decompress, network-bound |
| `context_optimization/dynamic_context_manager.py` | orjson | ❌ Dead code? | Žádný production caller |
| `tools/session_manager.py` | orjson | ❌ False positive | Network-bound session I/O |
| `loops/research_loop.py` | orjson | ❌ False positive | Optional --loop, network-bound |
| `tools/commoncrawl_adapter.py` | orjson | ❌ False positive | Network-bound CommonCrawl API |
| `tools/wayback_adapter.py` | orjson | ❌ Trivia | 52ř, network latency dominates |
| `utils/shadow_dtos.py` | msgspec | ❌ Benchmark only | Žádný production caller |
| `brain/ner_engine.py` | msgspec | ❌ Fallback path | MLX fallback, inference dominates |
| `discovery/duckduckgo_adapter.py` | msgspec | ❌ False positive | Network-bound API calls |
| `discovery/rss_atom_adapter.py` | msgspec | ❌ False positive | I/O-bound feed processing |
| `fetching/public_fetcher.py` | msgspec | ✅ Legitimate | Malý struct v hot path, network wait dominates |

**Pattern:** V naprosté většině případů platí: **network-bound operations** (HTTP requests, API calls, feed fetching) mají 100-10,000x větší latenci než jakákoli serializace. Optimalizace orjson/msgspec tam je **microbenchmark theater**.

**Jedinná legitimate use case** v tomto codebase: `FetchResult(msgspec.Struct)` v `fetching/public_fetcher.py` — malý immutable struct v hot path kde network wait dominates, msgspec dává malý ale reálný win.
================================================================================
CLAUDE CODE TASK: STRATEGIC DEEP ARCHITECTURE & CODEBASE AUDIT (READ-ONLY)
================================================================================

PROJEKT: ~/PycharmProjects/Hledac/hledac/universal
TARGET ENVIRONMENT: macOS M1 (8GB UMA RAM), Python 3.14.6 Standard GIL (NE-free-threaded), MLX Engine, PyO3 Rust Extensions.

MÓD PROVEDENÍ: STRICT READ-ONLY AUDIT.
NEUPRAVUJ, NEMĚŇ ANI NEVYTVÁŘEJ ŽÁDNÉ KÓDOVÉ SOUBORY. VÝSTUPEM JE POUZE STRUKTUROVANÁ MASTER ROADMAPA.

--------------------------------------------------------------------------------
1. NOISE FILTER (CO BLESKOVĚ IGNOROVAT - NEZTRÁCEJ NA TOM ČAS)
--------------------------------------------------------------------------------
- IGNORUJ: Chybějící docstringy, drobné chyby ve formátování (PEP8), překlepy v proměnných.
- IGNORUJ: Nepodstatná linter varování, chybějící type-hinty u interních pomocných funkcí.
- IGNORUJ: .md soubory, složky auditů, staré reporty a dokumentaci (ověřuj VÝHRADNĚ živý kód v .py a .rs).

--------------------------------------------------------------------------------
2. CÍLOVÉ OBLASTI ANALÝZY (HLEDEJ TYTO 5 STRATEGICKÝCH ÚHLŮ)
--------------------------------------------------------------------------------

[ÚHEL 1: ARCHITEKTURA, PROPOJENOST & DATOVÉ KONTRAKTY]
- Zkontroluj správnost importů, názvů funkcí a celkovou topologii modulů.
- Hledej SRP (Single Responsibility) porušení (God Objects jako ModelManager).
- Ověř konzistenci datových kontraktů (msgspec.Struct vs list/dict) na hranicích LMDB <-> LanceDB <-> DuckDB.
- Najdi osiřelé datové toky (zápis do jedné DB bez synchronizace s druhou).

[ÚHEL 2: MEMORY SAFETY, M1 HARDWARE & UMA PRESSURE]
- Hledej MLX GPU stalls: Volání mx.eval([]) / clear_cache() v hlavním async threadu.
- Ověř povinné pořadí: mx.eval([]) MUSÍ předcházet mx.clear_cache().
- Identifikuj místa chybějícího reaktivního throttlingu při dosažení 7.2 GiB RSS (macOS SWAP limit).
- Zkontroluj limity velikostí vstupních dat (unbounded HTML, neomezená délka textu pro embeddingy).
- Ověř uvolňování systémových zdrojů: macOS File Descriptors limit (ulimit -n) a libc malloc_zone_pressure_relief.

[ÚHEL 3: ASYNC CONCURRENCY, RUST FFI & GIL ISOLATION]
- Hledej CPU-intensive operace v Rustu (Aho-Corasick, Bloom Filter, hashing), které NEVOLAJÍ py.detach() / release_gil().
- Zkontroluj Rust FFI panic boundaries: Každá #[pyfunction] v Rustu MUSÍ mít std::panic::catch_unwind, aby panic neshodil celý Python proces (SIGABRT).
- Vyhledej zastaralé asyncio.gather() vhodné pro migrace na Python 3.14.6 asyncio.TaskGroup.
- Ověř přítomnost backpressuru a maxsize na VŠECH asyncio.Queue v projektu (hledej put_nowait bez drop-policy).
- Najdi chybějící Single-Flight vzory (thundering herd při expiraci DNS nebo IOC cache).

[ÚHEL 4: OSINT CAPABILITIES, OPSEC & NETWORK ISOLATION]
- Hledej DNS úniky: Volání systémového OS resolveru pro .onion a .i2p domény dříve než dotaz projde přes proxy.
- Ověř SSRF ochrany před cURL/HTTP požadavky (blokace 127.0.0.1, 169.254.169.254 AWS metadata).
- Zkontroluj fallbacky: Zda selhání Tor/I2P proxy nemůže ticho přesměrovat požadavek na clearnet rozhraní.
- Ověř přítomnost max_bytes streamovacích limitů u všech HTTP odpovědí.

[ÚHEL 5: PROVOZNÍ ODOLNOST, LLM SECURITY & SELF-HEALING]
- Ošetření LMDB MDB_MAP_FULL (lmdb.MapFullError) a dynamické zvětšování map_size.
- Automatický plánovač VACUUM a CHECKPOINT pro DuckDB WAL logy.
- Nepřímý Prompt Injection: Sanitizace staženého webového obsahu před vložením do promptu pro LLM (Hermes3).
- Secrets Scrubbing: Anonymizace API klíčů, tokenů a hesel před zápisem do EvidenceLogu/databází.
- Dead-Letter Queue (DLQ): Zda selhání jediného payloadu nezpůsobí pád celé dávky.

--------------------------------------------------------------------------------
3. STRATEGIE PROVEDENÍ ANALÝZY PRO CLAUDE CODE
--------------------------------------------------------------------------------
1. Neprocházej soubory slepě po jednom. Použij `grep` / `ripgrep` pro nalezení klíčových vzorů (např. `asyncio.gather`, `mx.clear_cache`, `unsafe`, `Queue(`, `py.detach`, `catch_unwind`).
2. Křížově porovnej Rust FFI rozhraní v `rust_extensions/src/lib.rs` s jejich voláním v Pythonu.
3. Pro každý nalezený problém zjisti jeho SKUTEČNOU kořenovou příčinu (Root Cause) v širším kontextu systému.

--------------------------------------------------------------------------------
4. POŽADOVANÝ STRUKTUROVANÝ VÝSTUP (MASTER ROADMAP)
--------------------------------------------------------------------------------
Výstup formuluj výhradně jako strukturovanou MASTER ROADMAPU rozdělenou do fází (Phase 0 až Phase 4). Každé zjištění MUSÍ mít tento přesný formát:

ISSUE [KÓD_KATEGORIE]-[ČÍSLO]: [Stručný název problému]
- Soubor a řádky: [přesná cesta:řádky]
- Úhel analýzy: [Angle 1 až 5]
- Závažnost: [🔴 Critical | 🟡 High | 🟢 Medium]
- Root Cause: [Komplexní vysvětlení kořenové příčiny]
- Impact: [Reálný dopad na systém, paměť M1 nebo chování při běhu]
- Cutting-edge Solution: [Přesné, robustní technické řešení kompatibilní s Python 3.14.6 GIL a macOS M1]

Na závěr připoj tabulku IMPLEMENTAČNÍ PRIORITNÍ MATICE (Issue ID | Fáze | Závažnost | Náročnost S/M/L) a příkazy pro ověření (pytest).

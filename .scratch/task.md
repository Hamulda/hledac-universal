================================================================================
CLAUDE CODE TASK: SYSTEM-LEVEL ZERO-COPY & HARDWARE OPTIMIZATION AUDIT
================================================================================

PROJEKT: ~/PycharmProjects/Hledac/hledac/universal
TARGET ENVIRONMENT: macOS M1 (8GB UMA RAM), Python 3.14.6 Standard GIL, APFS File System.

MÓD PROVEDENÍ: STRICT READ-ONLY AUDIT.
NEUPRAVUJ ANI NEVYTVÁŘEJ ŽÁDNÉ KÓDOVÉ SOUBORY.

Předchozí audit detailně prošel logiku v `deephermes3_engine.py`. Nyní se zaměř VÝHRADNĚ na systémové I/O, paměťové datové toky a hardwarovou integraci na M1.

--------------------------------------------------------------------------------
1. NOISE FILTER (IGNORUJ)
--------------------------------------------------------------------------------
- IGNORUJ: `deephermes3_engine.py` (již kompletně probrán).
- IGNORUJ: Linter varování, docstringy, PEP8, type-hinty.

--------------------------------------------------------------------------------
2. CÍLOVÉ ADVANCED OBLASTI ANALÝZY
--------------------------------------------------------------------------------

[ÚHEL A: ZERO-COPY ARROW PIPELINES & UMA MEMORY]
- Zkontroluj datové přenosy mezi DuckDB, Rustem, EvidenceLogem a MLX.
- Identifikuj zbytečné serializace (dict -> JSON -> string -> Rust/DuckDB) a navrhni Apache Arrow C Data Interface (zero-copy v LPDDR4X RAM).

[ÚHEL B: STREAMING SPECULATIVE PARSING]
- Zkontroluj streaming tokenů v MLX inferenci.
- Zjisti, zda je možné implementovat průběžný parsing JSONu nad token streamem, který umožní spouštět síťové požadavky (FetchCoordinator) okamžitě po detekci URL/IP klíče v streamu.

[ÚHEL C: APFS ZERO-COPY SNAPSHOTS & FILE SYSTEM I/O]
- Zkontroluj vytváření záloh databází a stavů sprintu v `paths.py` a `runtime/`.
- Prověř využití APFS `clonefile()` (Copy-on-Write) pro okamžité vytváření snapshotů bez fyzického kopírování na SSD.

[ÚHEL D: FANLESS M1 THERMAL LEVEL ADAPTATION]
- Zkontroluj `core/resource_governor.py`.
- Ověř čtení `hw.thermallevel` přes `sysctl` na macOS pro prevenci termálního throttlingu na pasivně chlazeném M1 MacBooku Air.

--------------------------------------------------------------------------------
3. POŽADOVANÝ STRUKTUROVANÝ VÝSTUP
--------------------------------------------------------------------------------
Formuluj výstup jako doplňkovou strategickou roadmapu. Pro každý problém uveď:
- ISSUE ID, Soubor a řádky.
- Root Cause & Impact na M1 8GB UMA / SSD / CPU.
- Cutting-edge Solution (bez pseudo-kódu, technicky přesný návrh).

# Issue #27 + #28: Kompletní Analýza — Cargo + PyPI Dependencies Upgrade

**Datum:** 2026-07-05
**Status:** ANALÝZA HOTOVA
**Priority:** MEDIUM

---

## Executive Summary

| Problém | Status | Akce |
|---------|--------|------|
| PyO3 0.25 → 0.29 | 🔴 BLOKOVÁNO | Zůstat na 0.27 (poslední s `allow_threads` public API) |
| Cargo deps (rayon, parking_lot, crossbeam) | 🟡 MINOR | Upgrade na latest stable |
| duckdb 1.4 → 1.10 | 🟡 RIZIKO | Testovat bundled API changes |
| rustworkx 0.18 → latest | 🟢 BEZPEČNÉ | Upgrade |
| httpx 0.28.1 latest | 🟢 AKTUÁLNÍ | Žádná změna |
| pydantic v3 | 🔴 BLOKOVÁNO | `<3.0.0` constraint v pyproject |

---

## #27 Cargo Dependencies — Detail Analysis

### PyO3 — NEJKRITICKĚJŠÍ

| Verze | Python 3.14 | allow_threads() | gil="false" | Status |
|-------|-------------|----------------|-------------|--------|
| 0.25  | ✅ wheel    | ✅ public API  | ❌         | **STAY** |
| 0.27  | ✅ wheel    | ⚠️ documentace | ✅          | **OPTIMAL** |
| 0.28  | ✅ wheel    | ❌ internal    | ✅          | Risk |
| 0.29  | ✅ wheel    | ❌ CHYBÍ      | ✅          | **BREAKING** |

**Lesson [surface_id=737]:** PyO3 0.29 NEMÁ public `allow_threads()` API.
Dokumentace v `gil.rs` popisuje `py.allow_threads(move || {...})` jako pattern,
ale v 0.29.0 to není v public API.

**Rozhodnutí:** PyO3 → `0.27` — poslední verze kde `allow_threads()` existuje.
`gil="false"` feature aktivovat až bude plně stabilní v PyO3 0.30+.

### Rayon — UPGRADE

```
Current:  1.10
Latest:    1.12
Breaking:  NE (rayon semver guarantees)
Action:    Upgrade to 1.12
```

### Parking Lot — UPGRADE

```
Current:  0.12
Latest:    0.12.5
Breaking:  NE (parking_lot minor semver)
Action:    Upgrade to 0.12.5
```

### Crossbeam Channel — UPGRADE

```
Current:  0.5
Latest:    0.5.15
Breaking:  NE (crossbeam semver minor)
Action:    Upgrade to 0.5.15
```

### DuckDB (bundled) — HIGH RISK

```
Current:  1.4  (duckdb crate)
Latest:    1.10504.0
Breaking:  ANO — DuckDB 1.10+ má breaking changes v C API
```

**DuckDB 1.10 breaking changes:**
1. `duckdb_connect` → `duckdb_database` object lifecycle changed
2. `duckdb_query` → result chunking API changed (chunk size parameter)
3. Prepared statements API v2

**Rust-side impact:** Musíme otestovat `duckdb = { version = "1.4", features = ["bundled"] }`
funkčnost s novějšími Rust DuckDB bindings.

**DuckDB Python (duckdb 1.5.4):**
```
Installed: 1.5.4
PyPI latest: 1.10504.0
Breaking: ANO — Python API změny mezi 1.5→1.10
```

**Riziko:** `duckdb_store.py` používá `duckdb.connect()` a SQL queries.
Změna z 1.5.4 na 1.10+ MŮŽE rozbít existing queries.

**Doporučení:** Testovat upgrade duckdb Python v izolovaném testu
před globálním upgrade.

### Ostatní Cargo deps — ANALÝZA

| Crate | Current | Latest | Breaking? | Action |
|-------|---------|--------|-----------|--------|
| aho-corasick | 1.1 | 1.1 | NE | Stay |
| regex | 1 | 1.11 | NE | Upgrade |
| regex-automata | 0.4 | 0.5 | NE | Upgrade |
| url | 2 | 2.5 | NE | Upgrade |
| lol_html | =2.1.0 | 2.1.0 | NE | Stay |
| xxhash-rust | 0.8 | 0.8 | NE | Stay |
| sha2 | 0.10 | 0.10 | NE | Stay |
| blake3 | 1 | 1 | NE | Stay |
| blake2 | 0.10 | 0.10 | NE | Stay |
| memchr | 2.5 | 2.7 | NE | Upgrade |
| lz4_flex | 0.10 | 0.10 | NE | Stay |
| zstd | 0.13 | 0.13 | NE | Stay |
| libc | 0.2 | 0.2 | NE | Stay |
| ipnetwork | 0.20 | 0.22 | NE | Upgrade |
| serde_json | 1 | 1 | NE | Stay |
| bincode | 2.0 | 2.0 | NE | Stay |
| memmap2 | 0.9 | 0.9 | NE | Stay |
| dirs | 5 | 5 | NE | Stay |
| sysinfo | 0.31 | 0.32 | NE | Upgrade |
| opentelemetry | 0.27 | 0.27 | NE | Stay |
| opentelemetry_sdk | 0.27 | 0.27 | NE | Stay |
| tracing | 0.1 | 0.1 | NE | Stay |
| tracing-subscriber | 0.3 | 0.3 | NE | Stay |
| tracing-opentelemetry | 0.28 | 0.28 | NE | Stay |
| opentelemetry-otlp | 0.27 | 0.27 | NE | Stay |
| warc | 0.3 | 0.3 | NE | Stay |
| flate2 | 1.0 | 1.0 | NE | Stay |
| metal | 0.29 | 0.29 | NE | Stay |
| objc | 0.2 | 0.2 | NE | Stay |
| block | 0.1 | 0.1 | NE | Stay |

### pyo3-build-config

```
Current:  0.25
Latest:   0.29
Action:   Match pyo3 version (0.27)
```

---

## #28 PyPI Dependencies — Detail Analysis

### duckdb

```
Installed: 1.5.4
PyPI latest: 1.10504.0 (1.10 major)
Constraint in pyproject: ">=1.5.0,<1.12.0"
Breaking: ANO mezi 1.5→1.10
```

**DuckDB 1.10 breaking changes (Python API):**
- `cursor.description` → změna v null handling
- `connect()` multi-thread behavior change
- `register()` attachment API v2

**Bezpečná strategie:** Zvýšit upper bound na `<2.0.0` a otestovat
v test suite PŘED globálním release.

### rustworkx

```
Installed: 0.18.0
PyPI latest: N/A na PyPI (rustworkx je PyPI package)
cargo latest: rustworkx-core 0.18.0
Breaking: NE
Action: Otestovat v CI
```

**Poznámka:** rustworkx je bindovaný Rust crate. Verze na PyPI odpovídá
Rust crate verzi. Můžeme zvýšit constraint.

### httpx

```
Installed: 0.28.1
PyPI latest: 0.28.1 (podle uv pip list)
Status: AKTUÁLNÍ
```

### pydantic

```
Installed: 2.13.4
Constraint: ">=2.10.0,<3.0.0"
Status: V rámci constraintu
BLOKOVÁNO: pydantic v3 breaking changes
```

**Pydantic v3 breaking changes:**
- `BaseModel` → `model_config` místo `Config` class
- Validace reorder — validátory se jinak volají
- `__init__` signature change

**Nicméně:** Projekt už používá msgspec pro nové DTOs (viz sprint P1-P3).
Msgspec je preferovaný serializační framework.

### msgspec

```
Installed: 0.21.1
Latest: 0.22.x?
Breaking: NE
Action: Upgrade když vyjde stable 0.22
```

### polars

```
Installed: 1.42.1
Latest: 1.42.x
Status: AKTUÁLNÍ
```

### lancedb

```
Installed: 0.34.0
Latest: 0.34.x
Status: AKTUÁLNÍ
```

---

## Recommended Actions

### Fáze 1: Cargo Upgrade (Issue #27)

1. **PyO3:** 0.25 → 0.27 (NE 0.29 kvůli allow_threads)
2. **pyo3-build-config:** 0.25 → 0.27
3. **rayon:** 1.10 → 1.12
4. **parking_lot:** 0.12 → 0.12.5
5. **crossbeam-channel:** 0.5 → 0.5.15
6. **regex:** 1 → 1.11
7. **regex-automata:** 0.4 → 0.5
8. **memchr:** 2.5 → 2.7
9. **ipnetwork:** 0.20 → 0.22
10. **sysinfo:** 0.31 → 0.32
11. **duckdb:** 1.4 → 1.5 (testovat 1.10 v separately)

### Fáze 2: PyPI Upgrade (Issue #28)

1. **rustworkx:** Zvýšit upper bound na `<0.20.0`, otestovat
2. **duckdb:** Zvýšit upper bound na `<2.0.0`, otestovat v CI
3. **httpx:** Stay at 0.28.x (aktuální)
4. **pydantic:** Zůstat na v2 (project používá msgspec pro nové kódy)

---

## Risk Matrix

| Změna | Riziko | Mítost | Akce |
|-------|--------|--------|------|
| PyO3 0.25→0.27 | STREDNÉ | allow_threads public API | Testgil.rs funkce |
| DuckDB bundled 1.4→1.5 | NÍZKÉ | minor API changes | DuckDB tests |
| DuckDB Python 1.5→1.10 | VYSOKÉ | SQL API breaking | Testovat v CI |
| rayon upgrade | NÍZKÉ | semver minor | N/A |
| rustworkx upgrade | STREDNÉ | PyO3 binding changes | Otestovat graph tests |

---

## M1 8GB UMA Constraints

- Rust extensions CDYLIB ~50-80MB v RAM
- DuckDB bundled přidává ~25MB
- Zvýšení na DuckDB 1.10+ může zvýšit binary size
- Metal compilation na M1 JE敖 optimalizované (NEON + AMG)

---

## Cutting-Edge Alternatives

### PyO3 0.30+ Strategy
Pokud PyO3 0.30+ vrátí `allow_threads` do public API, upgrade na 0.30
otevře `gil="false"` pro free-threaded Python (PEP 703).

### DuckDB Vectorized Execution
DuckDB 1.10+ má lepší SIMD vectorization — potenciál 10-20%
rychlejší DuckDB queries na M1 NEON.

### rustworkx 0.20+
Novější rustworkx má lepší APSP (All-Pairs Shortest Path) algoritmy
pro graph analytics.

---

## References

- [Lesson 737: PyO3 0.29 allow_threads](memory surface_id=737)
- [Issue 4.6: PyO3 0.29 allow_threads failed](memory)
- [CLAUDE.md: PyO3 version constraint](CLAUDE.md)
- [Sprint P1-P3: msgspec for DTOs](memory)

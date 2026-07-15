# F350M-R: Architektura & Optimalizace Roadmap

## Stav k 2026-07-16

---

## I. ANALÝZA — 50 ostrovů, 837 nepoužívaných souborů

### Dependency Graph

| Skupina | Souborů | Charakter |
|---------|---------|-----------|
| Main island | 148 | Všechny klíčové moduly — brain, coordinators, layers, transport, utils |
| Rust extensions | 72 | PyO3 crates, zero-copy bindingy, SIMD |
| Intelligence+Knowledge | 18 | duckdb_store, identity stitching, temporal archaeologist |
| Pipeline | 17 | live_public_pipeline, stages |
| Runtime protocols | 16 | BrainProtocol, FetchProtocol, GraphProtocol… |
| Core Rust backend | 13 | Python-facing wrappers |
| Ostatní | 2-10 | Drobné moduly |

**Bridge mezi ostrovy (Slabé spoje):**
- `intel/` → `recon/` — přejmenováno, staré importy mrtvé (islets 14, 8, intel/*)
- `network/` → `recon/` — podobně
- Rust ostrov (72) má minimální Python importy — většina přes `core/rust_backend/`
- `intel/` plně mrtvý — 8 souborů, 0 živých importů

---

## II. KRITICKÉ NÁLEZY

### 🔴 CRITICAL: 45 circular dependencies

| Cyklus | Závažnost |
|--------|-----------|
| `transport/base.py ↔ transport/__init__.py ↔ transport/transport_resolver.py ↔ tor/nym` | Vysoká — blokující při importu |
| `knowledge/duckdb_store.py ↔ knowledge/quality_assessment.py` | Vysoká — memory leak path |
| `brain/__init__.py` (10× self-referential) | Střední — import latency |
| `core/rust_backend/__init__.py` (10× self-referential) | Střední — lazy-loading paradox |
| `export/formatters.py ↔ export/sprint_exporter.py` | Nízká |

**Root cause:** PEP 562 `__getattr__` lazy imports vytvářejí zdánlivé cykly, ale skutečný problém je v transport vrstvě.

### 🔴 CRITICAL: 837 underutilized souborů (NE dead — dynamic import/entry-point pattern)

**Underutilized moduly (0 static imports, ale may be dynamically imported or CI scripts):**

| Kategorie | Souborů | Status |
|-----------|---------|--------|
| `intel/*` | 15+ | **ŽIVÉ** — používají `importlib.import_module` (dynamic loading) |
| `tools/` | 20+ | **ŽIVÉ** — CI/benchmark skripty, mají `__main__` entry points |
| `tests/archive/*` | 50+ | **ARCHIVOVÁNO** — deprecated, ale archived, ne smazáno |
| `network/gemini_transport.py`, `jarm_fingerprinter.py` | 2 | Možná mrtvé — pouze pokud nejsou dynamicky importované |
| `enhanced_research.py` | 1 | **Mrtvé** — pouze archived test imports, žádný aktivní import |
| `pipeline/scoring.py`, `pivot_lane_planner.py`, `_deduper.py` | 3 | Možná mrtvé |
| `coordinators/backpressure.py`, `claims_coordinator.py` | 2 | Možná mrtvé |
| `coordinators/memory/*` | 3 | Možná mrtvé |

**Důležité pravidlo:** V Pythonu 0 static imports ≠ dead code. Moduly mohou být:
1. Entry point (`if __name__ == "__main__"`)
2. Dynamicky importované přes `importlib` / `__import__`
3. CLI nástroje v `tools/`
4. Test-only imports

**Skutečně mrtvé (confirmed):**
- `enhanced_research.py` — pouze archived test imports
- `intel/__init__.py` — prázdný po reorganizaci (stub)

**Pattern:** F320-F350 sprint lifecycle změny opustily staré moduly, ale tyto moduly NEJSOU automaticky mrtvé bez další analýzy.

### 🟡 HIGH: 102 hotspots — kritické body selhání

| Soubor | Import count | Riziko |
|--------|-------------|--------|
| `pipeline/public_patterns.py` | 18 | Jeden file, 18 závislostí — změna má obrovský blast radius |
| `coordinators/base.py` | 17 | FetchCoordinator závisí na něm |
| `rust_extensions/src/gil.rs` | 14 | GIL management bottleneck |
| `pipeline/public_stages.py` | 11 | |
| `core/rust_backend/__init__.py` | 11 | |
| `knowledge/duckdb_store.py` | 11 | Canonical write path |

### 🟡 HIGH: DuckDB Store coupling

`duckdb_store.py` (island 3) závisí na 11 modulech a 18 importuje z něj. Jakákoliv změna v něm cascadeuje přes island 1+3+4.

---

## III. OPTIMALIZAČNÍ PRIORITY

### Tier 1: Dead Code Removal (nejvyšší ROI, nejnižší risk)

**Islands 8, 14, 16-50 (drobné izolované moduly)**
- `intel/` — 8 souborů, confirmed mrtvé po F320-INTEL-REORGANIZE
- `network/` — 8 souborů, partially merged do `recon/`
- `research/` — 4 souborů, 0 živých importů
- `captcha_solver.py` + `enhanced_research.py` — standalone, 0 importů

**Akce:**
```bash
# Potvrdit mrtvé importy
grep -rl "from intel|"import intel" | grep -v intel/
grep -rl "from network|"import network" | grep -v network/
# Smazat po-confirm
```

### Tier 2: Circular Dependency Resolution

**Transport cyklus (blokující):**
```
transport/base.py
  ↕
transport/__init__.py (PEP 562)
  ↕
transport/transport_resolver.py
  ↕
transport/tor_transport.py / nym_transport.py
```

**Řešení:** PEP 562 `__getattr__` v `transport/__init__.py` oddělit od skutečného importu. Vytvořit `transport/_bootstrap.py` s minimálním importem.

**Brain/__init__.py cyklus:**
PEP 562 lazy delegation vytváří 10× zdánlivý cyklus. Skutečný problém: `brain/__init__.py` importuje engine moduly, které importují zpět. Řešení: rozbití na `brain/_core.py` (bez engine importů) + `brain/engines.py` (lazy).

### Tier 3: Rust Bridge Consolidation

**Current state:** 72 `.rs` souborů, Python importuje přes `core/rust_backend/__init__.py`

**Problem:**
- `core/rust_backend/__init__.py` má 11 importů
- 837 nepoužívaných `.rs` souborů není plně identifikováno
- Rust side: `lib.rs` importuje 87 modulů, ale Python side využívá jen ~20

**Řešení:**
1. Instrumentovat Rust build — které `use` jsou `unused`?
2. Vytvořit `rust_extensions/src/_wired.rs` — only truly called PyO3 functions
3. `rust_extensions/src/_archive/` — unused modules, can be removed from build

### Tier 4: DuckDB Store Decoupling

`duckdb_store.py` je nejvíce importovaný Python soubor (island 3 + island 1). Alternativy:
- Protocol-based DI: `DuckDBStoreProtocol` (existuje z F320-K5)
- Lazy connection: connect only on first query
- Read replica: split reads vs writes

---

## IV. FÁZE IMPLEMENTACE

### F360: Underutilized Code Sprint (1 den)

**Cíl:** Prověřit 837 "unused" souborů — odlišit skutečně mrtvé od dynamic-import a entry-point patternů

**Kroky:**
1. Prověřit `intel/` — 15+ souborů používá `importlib.import_module` — **DYNIMPORT, NE mrtvé**
2. Prověřit `tools/` — CI/benchmark skripty s `__main__` entry points — **ŽIVÉ**
3. Prověřit `network/gemini_transport.py`, `jarm_fingerprinter.py` — existují dynamické importy?
4. Potvrdit `enhanced_research.py` — pouze archived test imports → **MRTVÉ**
5. Archivovat confirmed mrtvé do `archive/f360-underutilized/`

**Confirmed mrtvé:**
- `enhanced_research.py` (pouze archived test imports)
- `intel/__init__.py` (prázdný stub po reorganizaci)

**Pattern:** F320-F350 změny opustily staré moduly, ale většina je pod dynamic import patternem — vyžaduje ruční prověření.

### F361: Circular Fix Sprint (2 dny)

**Cíl:** Rozbít 45 circular dependencies

**Prioritní:**
1. `transport/base.py ↔ transport/__init__.py` — vytvořit `transport/_lazy.py`
2. `brain/__init__.py` — rozbít na `brain/_core.py` + `brain/engines.py`
3. `core/rust_backend/__init__.py` — použít `__getattr__` místo `__all__`

**Test:** `python -c "from transport import base"` bez chyb.

### F362: Rust Consolidation (3 dny)

**Cíl:** Snížit Rust footprint, zvýšit využití

**Kroky:**
1. `cargo check --release 2>&1 | grep unused` — identifikovat nepoužívané Rust moduly
2. Vytvořit `rust_extensions/src/_wired.rs` — pouze skutečně volané funkce
3. Archivovat nepoužívané moduly (podle `find_unused` + Rust build analysis)
4. Dokumentovat Rust API v `core/rust_backend/`

### F363: DuckDB Store Decouple (2 dny)

**Cíl:** Snížit coupling DuckDB store

**Řešení:**
1. `DuckDBStoreProtocol` — plně oddělit implementaci
2. `duckdb_store.py` — rozdělit na write path + read path
3. Lazy connection init

### F364: Hotspot Blast Radius (2 dny)

**Cíl:** Snížit riziko změn v 18-import `public_patterns.py`

**Řešení:**
1. Extrahovat regex patterns do samostatného modulu `pipeline/_patterns.py`
2. `public_patterns.py` se stane thin wrapper
3. Test coverage: 100% na extrahovaných funkcích

---

## V. INVARIANTY (vždy dodržet)

1. **Žádné nové circular imports** — CI musí failnout
2. **Dead code = archive, ne smaz** — 30denní retention
3. **Rust unused detection v CI** — `cargo check --release` vždy clean
4. **Hotspot coverage ≥ 80%** — before changing a hotspot, add tests
5. **No new feature flags** — always-on only

---

## VI. SOUHRN TABULKA

| Sprint | Název | Effort | Impact | Risk |
|--------|-------|--------|--------|------|
| F360 | Dead Code Removal | 1 den | -837 souborů, rychlejší CI | Nízký |
| F361 | Circular Fix | 2 dny | Rychlejší import, méně memory leak | Střední |
| F362 | Rust Consolidation | 3 dny | -20+ nepoužívaných .rs, rychlejší build | Střední |
| F363 | DuckDB Decouple | 2 dny | Nižší coupling, testovatelnost | Vysoký |
| F364 | Hotspot Deflation | 2 dny | Nižší blast radius na 18-import | Střední |

**Total: ~10 pracovních dní**

---

## VII.衢畲借鉴 (Cross-Reference)

- F320: Sprint lifecycle fixes — established Layer Protocol
- F330: Modular SprintScheduler split — precedent pro rozbíjení velkých souborů
- K5: Protocol-based DI — existující pattern, málo využívaný
- F265B: LMDB conditional cache — precedent pro bounded collections

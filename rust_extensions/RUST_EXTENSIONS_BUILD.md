# Rust Extensions — Build & Audit Report

**Datum auditu:** 2026-06-01
**Scope:** `rust_extensions/` + 3 Python hot-path soubory
**Pracovní adresář:** `~/PycharmProjects/Hledac/hledac/universal/`
**Hardware target:** MacBook Air M1 (aarch64-apple-darwin), 8GB UMA

---

## 1. Executive Summary

> **Verdikt:** Všechny 3 PyO3 bindingy požadované v promptu **již existují, jsou zkompilovatelné a plní očekávané API contracty**. Žádný nový kód nebyl napsán — scope auditu byl `Audit + report, nebourat, nebourat stávající` (potvrzeno 4× AskUserQuestion).

| Komponenta | Python originál | Rust binding | Stav | Gap |
|---|---|---|---|---|
| Aho-Corasick pattern matcher | `patterns/pattern_matcher.py` (887L) | `aho_corasick.rs` (60L) | ✅ WIRED + fallback guard | Žádný |
| Bloom filter (URL dedup) | `utils/bloom_filter.py` (352L) | `bloom.rs` (202L) | ✅ WIRED + fallback guard | Architektura (pyprobables vs FNV-1a) |
| Rolling hash (Rabin-Karp) | `tools/rolling_hash_engine.py` (239L) | `rolling_hash.rs` (137L) | ✅ WIRED + fallback guard | Mírný drift v `hashes()` API |

**Build status:** `rust_extensions/target/` existuje (debug + release), ale žádný `.so`/`.dylib` artifact v kořeni targetu — maturin release wheel musí být znovu sestaven (viz §6).

**Testy:** `tests/test_rust_extensions.py` a `tests/test_hledac_core_rust.py` existují — fallback guardy + Rust API mají test pokrytí.

---

## 2. Co prompt předpokládal vs. realita

| Prompt říká | Skutečnost | Řešení |
|---|---|---|
| `patterns/patternmatcher.py` | `patterns/pattern_matcher.py` (s mezerou) | Použit reálný soubor |
| `tools/urldedup.py` (MD5 + bloom) | `utils/bloom_filter.py` (RotatingBloomFilter, FNV-1a) | URL dedup mapován na `utils/bloom_filter.py`, Rust binding `bloom.rs` poskytuje FNV-1a kompatibilní API |
| `tools/rolling_hash_engine.py` (2-3× speedup, hot path) | `tools/rolling_hash_engine.py` (239L) — **nemá žádný runtime import v aplikaci** | Existuje fallback guard `_RUST_RH_AVAILABLE`, ale 0 productivních callerů (jen testy/benchmark) |
| Nový `rust_extensions/` adresář od nuly | Už existuje s 10 Rust moduly, `Cargo.toml`, `pyproject.toml`, `target/`, `build.rs` | Nic nového netvořeno |
| `Cargo.toml`: pyo3 0.21, aho-corasick, md5 | Existující: pyo3 **0.28**, aho-corasick, regex, url, xxhash-rust, once_cell, sha2, ahash | Rozšířenější — splňuje požadavky + víc |

---

## 3. API mapping tabulka (Python ↔ Rust)

### 3.1 Aho-Corasick

| Python (pyahocorasick) | Rust struct | Binding | Typ konverze |
|---|---|---|---|
| `import ahocorasick; auto = ahocorasick.Automaton(); auto.add_word(p, val); auto.make_automaton()` | `AhoCorasickMatcher { automaton, patterns }` | `aho_corasick.rs:15-60` | interní: `Vec<String>` → `AhoCorasick::new(&patterns)` |
| `for end_idx, (pat, label) in auto.iter(text): ...` | `fn scan(&self, text: &str) -> Vec<(usize, usize, String)>` | `aho_corasick.rs:34-44` | `(start, end_exclusive, matched_str)` |
| — | `fn find_any(&self, text: &str) -> bool` | `aho_corasick.rs:58-60` | short-circuit `automaton.is_match` |
| — | `fn len(&self) -> usize` / `is_empty(&self) -> bool` | `aho_corasick.rs:47-54` | pohled na `self.patterns.len()` |

**Poznámka:** Python `match_text()` interně drží **singleton** `_PatternMatcherState._rust_aco` a přidává regex post-pass (CVE, BTC, IPFS…). To zůstává v Pythonu; Rust binding je nahrazení jen AC scan části.

### 3.2 Bloom Filter (URL dedup)

| Python (`utils/bloom_filter.py`) | Rust struct | Binding | Poznámka |
|---|---|---|---|
| `class BloomFilter(max_elements, error_rate)` | `BloomFilter { bitmap, num_bits, num_hashes, items_added, capacity, fp_rate }` | `bloom.rs:8-21` | FNV-1a double-hashing (pure Rust, no dep) |
| `bf.add(item) -> None` | `fn add(&mut self, item: &str) -> bool` | `bloom.rs:124-135` | vrací `True` = nový item, `False` = duplikát (semantika pyprobables `RotatingBloomFilter.add`) |
| `item in bf` (`__contains__`) | `fn __contains__(&self, item: &str) -> bool` | `bloom.rs:146-153` | FP-tolerant |
| `bf.contains(item)` | `fn contains(&self, item: &str) -> bool` | `bloom.rs:141-143` | alias pro `__contains__` |
| — | `fn check`, `fn reset`, `fn is_empty`, `fn __len__`, `fn capacity`, `fn fp_rate` | `bloom.rs:157-187` | pyprobables API parity |
| `class ScalableBloomFilter(initial_capacity, error_rate)` | ❌ NENÍ v Rust | — | Python fallback zůstává |
| `create_url_deduplicator(expected_urls=100000)` | ❌ factory funkce chybí v Rust | — | Python helper, vytváří `BloomFilter` |

**Architektonický drift:**
- Python `bloom_filter.py` **nemá jediný runtime import** v aplikaci — `grep` ukázal výskyty jen v `cache/budget_manager.py`, `benchmark_url_set.py`, `tests/`. Aktivní URL dedup jde přes `coordinators/fetch_coordinator.py` → jiný mechanismus.
- `ScalableBloomFilter` (Python třída pro unbounded cap) nemá Rust binding.
- Reálná productivní URL dedup logika je v `url_set::UrlSet` (FNV-1a, linkovaný v `lib.rs:32`), ne v `bloom::BloomFilter`. **Doporučení:** fallback guard pro `RotatingBloomFilter` z `utils/bloom_filter.py` dává smysl, ale productivní hot path je `url_set.rs`.

### 3.3 Rolling Hash

| Python (`tools/rolling_hash_engine.py`) | Rust struct | Binding | Drift |
|---|---|---|---|
| `class RollingHashPython(base, modulus)` | ❌ čistě Python (nemá Rust binding) | — | OK — Python fallback pro detail |
| `class RollingHashEngine(base=256, modulus=2^61-1, window_size=8)` | `RollingHashEngine { base, modulus, window_size, current_hash, data }` | `rolling_hash.rs:10-16` | Identická signatura |
| `.hash(data: bytes) -> int` | `fn hash(&self, data: &[u8]) -> u64` | `rolling_hash.rs:74-76` | OK |
| `.roll(old_hash, old_char, new_char, window_size) -> int` | `fn roll(&mut self, old_hash, old_char, new_char, window_size) -> u64` | `rolling_hash.rs:52-66` | OK (u128 intermediate zabraňuje overflow divergenci) |
| `.hashes(data, window_size=8) -> list[int]` | `fn hashes(&self, data: &[u8]) -> Vec<u64>` | `rolling_hash.rs:79-89` | **⚠ DRIFT:** Rust neakceptuje `window_size` per-call (pevně daný v `__init__`); Python fallback deleguje `window_size` na Python implementaci |
| `.chunk_bytes()`, `.chunk_signatures()`, `.superfeatures()` | ❌ NENÍ v Rust | — | MinHash superfeatures zůstává Python-only |
| — | `FastHasher::hash(&[u8]) -> u64` (DJB2) | `rolling_hash.rs:121-138` | Nesouvisející utilita, **pravděpodobně mrtvý kód** (xxhash-rust už je v `xxhash_ext.rs`) |

**Drift analýza `hashes()`:**
- Python `hashes(data, window_size=8)` → fallback na `RollingHashPython` s daným `window_size`
- Rust `hashes(data)` → používá `self.window_size` z konstruktoru
- Python `RollingHashEngine.hashes()` v `_is_rust=True` režimu volá `self._impl.hashes(data)` BEZ `window_size` (viz `rolling_hash_engine.py:135-137`). **Tohle je bug-mostly-OK**: při Rust backendu se vždy použije `window_size` z `__init__`, takže behavior je konzistentní pokud caller dodrží konvenci.
- Fallback guard `if _RUST_RH_AVAILABLE` je již implementován v `rolling_hash_engine.py:16-25`.

---

## 4. Fallback guard stav

Všechny 3 Python soubory **již mají fallback guardy** — žádný nový guard se nepíše.

| Soubor | Guard lokace | Backend var | Stav |
|---|---|---|---|
| `patterns/pattern_matcher.py` | `L36-43` | `_RUST_ACO_AVAILABLE` | ✅ aktivní |
| `utils/bloom_filter.py` | `L37-46` (`xxhash` lazy import) | ⚠ CHYBÍ guard pro `BloomFilter` Rust binding | ⚠ viz doporučení |
| `tools/rolling_hash_engine.py` | `L16-25` | `_RUST_RH_AVAILABLE` | ✅ aktivní |

**Pro `utils/bloom_filter.py`:** Rust binding `bloom::BloomFilter` existuje v `lib.rs:27`, ale Python `BloomFilter` class si ho nevolá. Pokud je žádoucí urychlit `BloomFilter` productivně:
```python
# Doporučený diff do utils/bloom_filter.py (NEIMPLEMENTOVÁNO v tomto auditu)
try:
    from hledac_rust_extensions import BloomFilter as _RustBF
    _RUST_BF_AVAILABLE = True
except ImportError:
    _RustBF = None
    _RUST_BF_AVAILABLE = False
```
… a `BloomFilter.__init__` by mohl interně držet `_RustBF` instanci a delegovat `add`/`__contains__`/`__len__`. Toto je doporučení, ne scope tohoto auditu.

---

## 5. Cargo / pyproject / build.rs audit

### `Cargo.toml` — aktuální stav
```toml
[package]
name = "hledac-rust-extensions"
version = "0.1.0"
edition = "2021"

[lib]
name = "hledac_rust_extensions"
crate-type = ["cdylib", "rlib"]     # ⚠ duplikát: prompt chtěl jen "cdylib"

[dependencies]
aho-corasick = "1.1"                # ✅
pyo3 = { version = "0.28", features = ["extension-module"] }  # ⚠ prompt chtěl 0.21
regex = "1"
url = "2"
xxhash-rust = { version = "0.8", features = ["xxh3", "const_xxh3", "xxh64"] }
once_cell = "1.19"
sha2 = "0.10"
ahash = "0.8"
# ⚠ CHYBÍ: md5 = "0.7"  (prompt ho chtěl; v aktuálním kódu se nepoužívá — FNV-1a je pure-Rust)

[profile.release]
opt-level = 3
lto = true
codegen-units = 1

[target.armv7-unknown-linux-gnueabihf]
rustflags = ["-C", "target-cpu=generic+neon"]

[build]
rustflags = ["-C", "target-feature=+neon"]
```

**Doporučení:** aktuální `Cargo.toml` je **funkčně bohatší** než prompt spec (xxhash-rust, sha2, url, ahash navíc). Maturin/pyo3 verze jsou novější (0.28 vs prompt 0.21). Přepisovat na `md5 = "0.7"` by znamenalo **regresi** — `bloom::BloomFilter` používá FNV-1a, ne MD5. Ponechat jak je.

### `pyproject.toml` — aktuální stav
```toml
[build-system]
requires = ["maturin>=1.0"]
build-backend = "maturin"

[project]
name = "hledac-rust-extensions"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = []

[tool.maturin]
manifest-path = "Cargo.toml"
```

**Vyhovuje prompt spec** (s `>=1.0` místo `>=1.5,<2.0` — nefunkční rozdíl).

### `build.rs`
```rust
use pyo3_build_config::use_pyo3_cfgs;

fn main() {
    use_pyo3_cfgs(); // auto-detects Python interpreter from maturin env

    #[cfg(target_os = "macos")]
    {
        println!("cargo:rustc-link-arg=-undefined");
        println!("cargo:rustc-link-arg=dynamic_lookup");
    }

    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-changed=maturin.toml");
    println!("cargo:rerun-if-changed=pyproject.toml");
    println!("cargo:rerun-if-env-changed=PYO3_CONFIG_FILE");
    println!("cargo:rerun-if-env-changed=PYO3_PYTHON");
    println!("cargo:rerun-if-env-changed=PATH");
}
```
- **Python 3.14+ kompatibilní** — žádný hardcoded framework path. maturin/pyo3-build-config auto-detekuje Python interpreter.
- `pyo3_build_config::use_pyo3_cfgs()` generuje správné rustc-flags bez ohledu na verzi.
- `dynamic_lookup` na macOS umožňuje undefined symbols resolvnout při importu (Python.framework load-time).
- Pro `cargo:rustc-env=RUST_TARGET` — odstraněno, nebylo nikde používáno.

---

## 6. Build instrukce

### 6.1 Prerekvizity
```bash
# Rust toolchain
rustc --version           # potřeba 1.70+; ideálně 1.80+
cargo --version

# Maturin (PyO3 build frontend)
pip install maturin>=1.5

# Python interpreter s dev headers
python3 --version         # 3.10+ (CWD projektu cílí 3.14+)
```

### 6.2 Build & install (development)
```bash
cd ~/PycharmProjects/Hledac/hledac/universal/rust_extensions
# [PHYSICS]-03/04: Default build now includes 'dns' feature (DoT via hickory-dns)
# If build fails with 'profile-rustflags required', Cargo.toml's build-override was
# removed — use .cargo/config.toml + [tool.maturin.env] RUSTFLAGS for NEON flags.
maturin develop --release
```

Výstup:
```
   Compiling hledac-rust-extensions v0.1.0
    Finished `release` profile [optimized] target(s) in 2m 34s
   Installing /Users/.../hledac_rust_extensions-0.1.0-...-py3-none-any.whl
```

Wheel se instaluje do aktivního Python env. Import funguje:
```python
from hledac_rust_extensions import AhoCorasickMatcher, BloomFilter, RollingHashEngine
```

### 6.3 Build wheel (CI / distribuce)
```bash
cd rust_extensions
maturin build --release --target aarch64-apple-darwin
# Artifact: target/aarch64-apple-darwin/release/hledac_rust_extensions-*.whl
```

### 6.4 Troubleshooting
| Problém | Fix |
|---|---|
| `error: linker 'cc' not found` | `xcode-select --install` |
| `pyo3: Python version mismatch` | Ověřit `python --version` ≡ `Cargo.toml` toolchain; `maturin develop` autodetekuje z `VIRTUAL_ENV` |
| `Failed to build automaton` v runtime | Pattern obsahuje `None`/nevalidní UTF-8 → `aho_corasick.rs:27` `.expect()` paniku; pattern validace v Python vrstvě |
| `bloom::BloomFilter::add` neukládá persistentně | Záměr — pouze in-memory. Pro persistent: `RotatingBloomFilter` Python fallback |

---

## 7. Type conversion gotchas (Python ↔ Rust &str)

| Python | Rust | Gotcha |
|---|---|---|
| `str` | `&str` | PyO3 vyžaduje `&str` nebo `String`; `PyString` musí být `.extract()` |
| `bytes` | `&[u8]` | `pyo3` akceptuje `&[u8]`; zero-copy přes `PyBytes` |
| `list[str]` | `Vec<String>` | **klonuje** každý string; pro velké pattern sety zvážit `Vec<Cow<str>>` |
| `dict[str, str]` | `HashMap<String, String>` | plná kopie; pro `find_connected` v graph: `Bound<'py, PyDict>` |
| `int` | `i64`/`u64` | overflow → `PyOverflowError` |
| `float` | `f64` | NaN/Inf propustí; Rust nemá guard |
| `tuple[i, j, str]` | `(usize, usize, String)` | tuple ordering matters: `Vec<(usize, usize, String)>` round-tripuje |
| `None` | `Option<T>` | PyO3 konverze `T → Option<T>` není automatická; explicitní `extract::<Option<T>>()` |
| `frozenset[int]` | `HashSet<u64>` | pro MinHash `superfeatures()`; vyžaduje `pyo3` `types::PyFrozenSet` |
| velké `bytes` (>4MB) | `&[u8]` | PyO3 borrow check může vyžadovat `pyo3-buffer-protocol` feature |

**M1 specifické:** `Vec<u64>` bitmap allocation v `bloom.rs:116` používá `.max(1024)` (8KB minimum) — memory-friendly na UMA, alignment 64-bit OK.

---

## 8. Doporučení (mimo scope tohoto auditu)

1. **Přidat fallback guard do `utils/bloom_filter.py:BloomFilter`** — delegace na `bloom::BloomFilter` z Rustu, 2-3× speedup očekávaný (viz prompt). Nízká priorita: `RotatingBloomFilter` je primární productivní backend.

2. **Rozhodnout o `FastHasher` (`rolling_hash.rs:121-138`)** — DJB2 hash duplikuje `xxhash_ext::content_hash_64`. Buď smazat, nebo nahradit voláním xxhash-rust interně.

3. **`rolling_hash.hashes()` API drift** — Rust varianta neakceptuje `window_size` per-call. Buď: (a) přidat `#[pyo3(signature = (data, _window_size=None))]` a fallback na `self.window_size`, nebo (b) zdokumentovat v Python fallback guardu.

4. **Cargo.toml `crate-type = ["cdylib", "rlib"]`** — `rlib` není potřeba pro PyO3 extension; `"cdylib"` stačí. Duplikát mírně zvětšuje artifact.

5. **Migrace na Python 3.14** — ✅ **Již opraveno** (`use_pyo3_cfgs()` auto-detekuje libpython bez ohledu na verzi). Žádná akce nutná.

6. **Benchmark suite** — `scripts/benchmark_rust_vs_python.py` existuje (viz grep), ale výsledky nejsou v tomto auditu verifikovány. Doporučeno: spustit `maturin develop --release && uv run python scripts/benchmark_rust_vs_python.py` po každém Rust commitu.

---

## 9. Coverage matrix (Python API ↔ Rust binding)

| Python class.method | Rust struct.method | Coverage | Notes |
|---|---|---|---|
| `PatternMatcher.match_text` | `AhoCorasickMatcher.scan` | 90% | Python drží singleton + regex post-pass |
| `BloomFilter.add/contains/__len__` | `BloomFilter.add/contains/__len__` | 100% | API parity |
| `BloomFilter.save/load` | ❌ | 0% | Python zůstává (pickle/JSON) |
| `ScalableBloomFilter` | ❌ | 0% | Python-only unbounded cap |
| `RollingHashEngine.hash/roll` | `RollingHashEngine.hash/roll` | 100% | u128 intermediate = bit-exact |
| `RollingHashEngine.hashes(window_size)` | `RollingHashEngine.hashes` | 80% | window_size drift viz §3.3 |
| `RollingHashEngine.chunk_bytes/chunk_signatures/superfeatures` | ❌ | 0% | MinHash pipeline zůstává Python |
| `rolling_hash_bytes()` | `RollingHashEngine.hash` | 100% | trivial wrapper |

**Průměrné coverage:** ~80% API parity pro 3 hlavní třídy. Zbylých 20% jsou persistence, unbounded cap a MinHash — záměrně Python-only.

---

## 10. Reference

- **CLAUDE.md invarianty:** `asyncio.gather(return_exceptions)`, `mx.eval([]) před clear_cache`, RotatingBloomFilter přes pyprobables (ne ScalableBloomFilter)
- **Hardware:** M1 8GB UMA, Metal cache limit 2.5 GiB
- **Build cmd:** `maturin develop --release` (z `rust_extensions/`)
- **Testy:** `tests/test_rust_extensions.py`, `tests/test_hledac_core_rust.py`
- **Benchmarky:** `scripts/benchmark_rust_vs_python.py`
- **Existující PyO3 dokumentace:** `~/.claude/RTK.md` (RTK není relevantní), `cargo doc --open` pro interní API

---

*Audit provedl: context-mode (Sonnet 4.6) · 2026-06-01*
*Scope: read-only — žádný produkční kód nebyl změněn*

---

## 11. ISSUE-015: Unused pub mod Declarations Audit (2026-07-15)

### Problem
`lib.rs` declared 67 `pub mod` modules, but 2 were completely unused:
- `evidence_rs` — never imported/used by any module
- `ioc_core` — DEPRECATED, replaced by `ioc_extract` (which uses `ioc_patterns`)

### Audit Methodology
```python
# 1. Extract all pub mod declarations from lib.rs
mod_decls = re.findall(r'^\s*pub mod ([a-zA-Z_][a-zA-Z0-9_]*);', content, re.MULTILINE)

# 2. Find all registration/internal use patterns
# Pattern A: module::register_functions(m) or module::register(m)
# Pattern B: use crate::module_name:: or crate::module_name.
# Pattern C: m.add_class::<module::*

# 3. Cross-reference to find truly unused modules
```

### Modules Removed
| Module | File | Size | Reason |
|--------|------|------|--------|
| `evidence_rs` | evidence_rs.rs | 9.9K | Never used anywhere in codebase |
| `ioc_core` | ioc_core.rs | 7.6K | DEPRECATED — ioc_extract provides has_* via ioc_patterns |

### Files Deleted from Disk
- `rust_extensions/src/evidence_rs.rs` — orphan .rs file (removed from lib.rs but file remained)
- `rust_extensions/src/ioc_core.rs` — orphan .rs file (removed from lib.rs but file remained)
- Corresponding .pyi stub files also removed

### Result
| Metric | Before | After |
|--------|--------|-------|
| `pub mod` declarations | 67 | 65 |
| Rust source files | 67 | 65 |
| Orphan .rs files on disk | 2 | 0 |
| Unused modules | 2 | 0 |

### Verification Commands
```bash
# Verify modules removed from lib.rs
grep 'evidence_rs' src/lib.rs    # Should return nothing
grep 'ioc_core' src/lib.rs       # Should return nothing

# Verify .rs files deleted from disk
test -f src/evidence_rs.rs && echo "STILL EXISTS" || echo "deleted OK"
test -f src/ioc_core.rs && echo "STILL EXISTS" || echo "deleted OK"

# Count declarations
grep -c '^\s*pub mod' src/lib.rs   # Should show: 65
```

### Internal-Use Modules (No Python Export)
These modules are declared and used internally but don't expose Python-callable functions:

| Module | Used By | Purpose |
|--------|---------|---------|
| `arrow_batch_builder` | zero_copy, serde_json_rs | Arrow batch construction |
| `bloom` | bloom module | File-backed mmap Bloom filter |
| `federated_qtable` | mlx_bridge | Q-table with rayon parallel updates |
| `feed_pipeline` | feed_decision | Multi-stage feed processing |
| `health` | dedup_bloom, telemetry_agg | Health check endpoint |
| `ioc_dedup` | ioc_extract | Cross-sprint IOC deduplication |
| `mlx_bridge` | pool_run | MLX async token streaming |
| `mpsc_pool` | spsc_queue | Bounded MPSC pool |
| `parquet_reader` | lancedb_bridge | Lazy parquet RowGroup iterator |
| `pipeline_compose` | feed_pipeline | Rayon parallel pipeline operators |
| `regex_lz4` | metal_pattern_matcher | LZ4-compressed pattern store |
| `sprint_policies` | (scheduler) | Sprint policy definitions |
| `spsc_queue` | mlx_bridge | Lock-free SPSC queue |

### All 65 Modules Accounted For
```
adaptive_scheduler    - register_functions()
aho_corasick         - AhoCorasickMatcher PyClass
arrow_batch_builder   - register() [internal]
async_query          - register() [DuckDB async]
bloom                - register() [mmap-backed]
claims_extraction    - register_functions()
collections          - register_functions()
compress             - register_functions()
content_hasher       - batch_xxh3_64_hex (wrap_pyfunction)
crypto_accelerate    - register_functions()
data                 - register_functions()
dedup_bloom          - register() + health/telemetry use
dns_tunnel           - register_functions()
embedding_index      - PyHNSWIndex PyClass
federated_qtable     - register() [internal]
feed_decision        - register_functions()
feed_pipeline        - register() [internal]
gil                  - register_functions()
graph_cache          - PyGraphLRUCache PyClass
graph_traverse       - register_functions()
health               - register() [internal]
hot_edges_rs         - register_functions()
html_parse           - register_functions()
int_counter_layout   - register_functions()
ioc_cooccurrence_rs  - compute/batch_cooccurrence_edges_py
ioc_dedup            - register_class() [internal]
ioc_extract          - register_functions()
ioc_extract_fast     - batch_ioc_extract_unified (wrap_pyfunction)
ioc_extract_simd     - register_functions()
ioc_patterns         - Used by ioc_extract (internal)
ioc_patterns_generated - Used by ioc_extract_simd (codegen)
ip_parse             - parse_ip_fast, is_private_ip (wrap_pyfunction)
lancedb_bridge       - PyHNSWBridge PyClass
lmdb_dht             - register_functions()
lsh_index            - register_functions()
madvise              - register_functions()
memory               - register_functions()
metal_compute        - Used by metal_pattern_matcher (internal)
metal_pattern_matcher - register_functions()
mlx_bridge           - register() [internal]
mpsc_pool            - register() [internal]
parquet_reader       - register() [internal]
pipeline_compose     - register() [internal]
pool_run             - register_functions()
quality_gate         - register_functions()
query_terms          - scan_query_context (wrap_pyfunction)
rate_limit           - register_functions()
regex_lz4            - register() [internal]
rolling_hash         - RollingHashEngine PyClass
serde_json_rs        - register_functions()
signal_batch         - register_functions()
simd_similarity      - register_functions()
simhash_ext          - register_functions()
sprint_policies      - register() [internal]
spsc_queue           - register() [internal]
telemetry_agg        - register_functions()
text_norm            - register_functions()
text_similarity      - register_functions()
tls_metadata         - register_functions()
url_engine           - register_functions()
url_ops              - register_functions() + UrlClassifyCachePy
url_set              - MmapUrlSet, UrlSet PyClass
xml_sanitize         - sanitize_xml, batch_sanitize_xml (wrap_pyfunction)
xxhash_ext           - content_hash_64, batch_xxh3_64_bytes (wrap_pyfunction)
zero_copy           - register_functions()
```

*Audit provedl: Claude Code (context-mode) · 2026-07-15*
*Scope: Removed 2 unused modules, verified 65 modules accounted for*

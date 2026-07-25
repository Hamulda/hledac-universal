# rust_extensions — PyO3 Native Extensions pro Hledac

## Modul
Python/Rust extension via PyO3 (`cdylib`), build via `maturin develop` / `maturin build`.

## Build
```bash
cd rust_extensions

# Standard build (default: core + data) — DuckDB wired
maturin develop

# Fast dev build (bez DuckDB, bez Metal) — ~5× rychlejší
maturin develop --no-default-features --features ""

# Full build (vše) — pouze CI
maturin develop --features "full"

# Release wheel
maturin build --release
maturin build --release --features "full"
```

## Feature flags (D3 fix)

| Příkaz | Kompiluje | Rychlost |
|--------|-----------|----------|
| `--no-default-features --features ""` | Pouze core moduly | ~5× rychlejší |
| `default` (bez flagů) | core + data | Standard |
| `--features "full"` | Všechny moduly | ~3× pomalejší |

> **D6 (2026-07-16):** `ml` feature odstraněna — `metal` crate (~45s compile, ~3MB dylib) nebyl v produkci využíván. `gpu_batch_keyword_scan()` v `metal_pattern_matcher` nebyl nikdy volán. CPU fallback přes Aho-Corasick + rayon je dostatečný pro všechny workloady. Viz CLAUDE.md root sekce D6.

Kompilace: `opt-level = 1` v `[profile.dev]` (3-5× rychlejší incremental builds na M1).

## Struktura
```
rust_extensions/
├── pyproject.toml          # maturin konfigurace
├── Cargo.toml              # Rust dependencies
├── src/
│   ├── lib.rs              # #[pymodule] = hledac_rust_extensions
│   └── ...
└── hledac_rust_extensions/ # Python package (pro maturin python-source=".")
    ├── __init__.py
    └── hledac_rust_extensions.pyi
```

## Důležité
- PyO3 cdylib = `module-name = "hledac_rust_extensions"` v pyproject.toml
- Python package jméno = `hledac_rust_extensions` (PODTRŽÍTKO, ne hyphen)
- `extension-module` feature = nutný pro cdylib build (od PyO3 0.21)
- Stable ABI (abi3) wheel vyžaduje separátní maturin build setup — není v current scope

## Maturin Cheatsheet
| Příkaz | Účel |
|--------|------|
| `maturin develop` | Build + install do dev Python (RY)
| `maturin build` | Wheel do `target/maturin/` |
| `maturin build --release` | Release build |
| `maturin list-python` | Dostupné Python interpretry |

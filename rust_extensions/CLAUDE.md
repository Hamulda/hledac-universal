# rust_extensions — PyO3 Native Extensions pro Hledac

## Modul
Python/Rust extension via PyO3 (`cdylib`), build via `maturin develop` / `maturin build`.

## Build
```bash
cd rust_extensions
maturin develop        # pro vývoj (instaluje do PYTHONPATH)
maturin build          # wheel v target/maturin/
```

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
- abi3-py314 wheel = Python 3.14 stable ABI, backward compatible 3.10-3.13

## Maturin Cheatsheet
| Příkaz | Účel |
|--------|------|
| `maturin develop` | Build + install do dev Python (RY)
| `maturin build` | Wheel do `target/maturin/` |
| `maturin build --release` | Release build |
| `maturin list-python` | Dostupné Python interpretry |

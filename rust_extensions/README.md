# Rust Extensions for Hledac

PyO3/Maturin wrappers for M1 hot-path acceleration.

## Quick Start

```bash
cd rust_extensions
maturin develop  # Builds and installs in current venv
```

For release build:
```bash
maturin build --release
```

## Files

```
rust_extensions/
├── Cargo.toml          # Rust dependencies
├── pyproject.toml      # Maturin config
├── build.rs            # Build script
├── src/
│   └── lib.rs          # PyO3 modules: aho_corasick, bloom_filter, rolling_hash
└── RUST_EXTENSIONS_BUILD.md  # Detailed build instructions & benchmarks
```

## Dependencies

- Rust 1.70+
- maturin 1.0+
- Python 3.10+

```bash
pip install maturin
rustup target add aarch64-apple-darwin
```

## Crates

| Crate | Purpose |
|-------|---------|
| `pyo3` | Python bindings |
| `aho-corasick` | Pattern automaton (10-50x speedup) |
| `md-5` | Fast MD5 hashing (2-3x speedup) |
| `bloomfilter` | Bloom filter ops |

## Fallback

Python modules auto-detect and fall back to pure-Python if Rust unavailable:
- `pattern_matcher.py` → checks `_RUST_ACO_AVAILABLE`
- `url_dedup.py` → checks `_RUST_BLOOM_AVAILABLE`
- `rolling_hash_engine.py` → checks `_RUST_RH_AVAILABLE`
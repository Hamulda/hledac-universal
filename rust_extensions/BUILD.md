# Rust Extensions Build Guide

## Prerequisites

### macOS M1
```bash
# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source ~/.cargo/env

# Install maturin
uv tool install maturin
# or
pip install maturin>=1.0.0
```

### Verify Toolchain
```bash
rustc --version
cargo --version
maturin --version
```

## Build

1. Navigate to rust_extensions:
```bash
cd rust_extensions
```

2. Ensure `hledac/` parent package is importable:
```bash
# Add to PYTHONPATH so hledac.rust_extensions can be imported
export PYTHONPATH=/path/to/hledac:$PYTHONPATH
```

3. Build and install:
```bash
unset UV_PYTHON  # Required if UV_PYTHON=3.14 is set
maturin develop
```

## Verify Installation

```bash
python -c "
from hledac.rust_extensions import (
    RustAhoCorasickMatcher,
    RustRotatingBloomFilter,
    RustRollingHashEngine,
)
print('SUCCESS: Rust extensions imported')

# Quick functionality test
m = RustAhoCorasickMatcher(['test', 'hello'])
print(f'AhoCorasick: {m.scan(\"hello world test\")}')

bf = RustRotatingBloomFilter(1000, 0.01)
bf.add('example.com')
print(f'BloomFilter: {bf.contains(\"example.com\")}')

rh = RustRollingHashEngine()
print(f'RollingHash: {rh.hash(b\"hello\")}')
"
```

## Python Integration

The Python fallback code checks for Rust availability:

```python
from hledac.rust_extensions.aho_corasick import RustAhoCorasickMatcher
_RUST_ACO_AVAILABLE = True  # Set when import succeeds
```

## Troubleshooting

**M1 build err "dependency graph has a cycle"**
→ Delete `rust_extensions/Cargo.lock` and retry

**PyO3 version mismatch**
→ Ensure pyproject.toml maturin>=1.0.0 matches Cargo.toml PyO3 version

**Extension not loading**
→ Ensure `hledac/` pkg is importable (add to PYTHONPATH)

**"No virtual environment found for Python 3.14"**
→ Unset `UV_PYTHON` environment variable before building:
```bash
unset UV_PYTHON
maturin develop
```
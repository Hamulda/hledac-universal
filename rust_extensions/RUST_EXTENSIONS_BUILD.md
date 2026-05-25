# Rust Extensions Build Guide

PyO3/Maturin wrappers for Hledac hot-path components.

## Build Requirements

- Rust 1.70+ (install via `rustup`)
- maturin 1.0+ (`pip install maturin`)
- Python 3.10+

## Quick Start

```bash
cd rust_extensions
maturin develop
```

This builds and installs the `hledac.rust_extensions` package into the current Python environment.

## Build for Release

```bash
maturin build --release --target aarch64-apple-darwin
```

Artifact: `target/aarch64-apple-darwin/release/hledac_rust_ext.*.so`

## Testing

```bash
# Verify Rust extension loads
python -c "from hledac.rust_extensions import aho_corasick, bloom_filter, rolling_hash; print('OK')"

# Benchmark Aho-Corasick
python -c "
from hledac.rust_extensions.aho_corasick import build_aho_corasick
import time
patterns = ['apt', 'cve-', 'microsoft'] * 100
text = 'apt microsoft cve-2024 adobe oracle' * 1000
t0 = time.perf_counter()
for _ in range(100):
    matcher = build_aho_corasick(patterns)
    matches = matcher.match_text(text)
t1 = time.perf_counter()
print(f'Rust: {(t1-t0)*1000:.2f}ms')
"

# Benchmark Bloom filter
python -c "
from hledac.rust_extensions.bloom_filter import RustRotatingBloomFilter
bf = RustRotatingBloomFilter(est_elements=100000)
for i in range(10000):
    bf.add(f'https://example.com/page/{i}')
print(f'Add: {bf.len()} entries')
print(f'Contains: {\"https://example.com/page/5000\" in bf}')
"
```

## Module API

### aho_corasick

```python
from hledac.rust_extensions.aho_corasick import build_aho_corasick, aho_corasick_match

# Class-based (stateful)
matcher = build_aho_corasick(['apt', 'cve-', 'microsoft'])
matches = matcher.match_text('apt cve-2024 microsoft')

# Functional (stateless)
matches = aho_corasick_match('apt cve-2024', ['apt', 'cve-'])
```

### bloom_filter

```python
from hledac.rust_extensions.bloom_filter import RustRotatingBloomFilter, md5_hash_str

# Bloom filter with MD5 hashing
bf = RustRotatingBloomFilter(est_elements=100000)
bf.add('https://example.com')
assert 'https://example.com' in bf

# Standalone MD5
h = md5_hash_str('hello')
```

### rolling_hash

```python
from hledac.rust_extensions.rolling_hash import RustRollingHashEngine, rabin_fingerprint

# Content-defined chunking
engine = RustRollingHashEngine(min_size=2048, avg_size=8192, max_size=65536)
chunks = engine.chunk_bytes(data, max_chunks=2048)

# Rabin fingerprint
hashes = rabin_fingerprint(data, window_size=48)
```

## Fallback Pattern

Python modules fall back to pure-Python if Rust extension unavailable:

```python
# patterns/pattern_matcher.py
try:
    from hledac.rust_extensions.aho_corasick import RustAhoCorasickMatcher
    _RUST_ACO_AVAILABLE = True
except ImportError:
    _RUST_ACO_AVAILABLE = False

# tools/url_dedup.py
try:
    from hledac.rust_extensions.bloom_filter import RustRotatingBloomFilter
    _RUST_BLOOM_AVAILABLE = True
except ImportError:
    _RUST_BLOOM_AVAILABLE = False

# tools/rolling_hash_engine.py
try:
    from hledac.rust_extensions.rolling_hash import RustRollingHashEngine
    _RUST_RH_AVAILABLE = True
except ImportError:
    _RUST_RH_AVAILABLE = False
```

## Benchmarks (Before/After)

Expected speedups on M1:

| Component | Current | Rust | Speedup |
|-----------|---------|------|---------|
| Aho-Corasick build+match | ~50ms | ~2ms | 25x |
| Bloom filter add/check | ~1ms | ~0.4ms | 2.5x |
| Rolling hash (1MB) | ~120ms | ~40ms | 3x |

### Aho-Corasick (most impactful)

```
Python (pyahocorasick):   ~50ms for 1000 patterns on 50KB text
Rust (aho-corasick crate): ~2ms  (same input)
```

### Bloom Filter

```
Python (probables):  ~1ms per add, ~0.5ms per check
Rust (custom):        ~0.4ms per add, ~0.2ms per check
```

### Rolling Hash

```
Python (pure):        ~120ms for 1MB data
Rust (pure):          ~40ms for 1MB data
```

## Troubleshooting

### maturin not found

```bash
pip install maturin
```

### Rust target not found

```bash
rustup target add aarch64-apple-darwin
```

### Build fails with linker error

Ensure Python dev headers installed:
```bash
brew install python@3.10  # macOS
apt install python3-dev   # Linux
```

## Cargo Dependencies

```
pyo3 = "0.22"           # Python bindings
aho-corasick = "1.1"    # AC automaton
md-5 = "0.10"          # MD5 (no-std compatible)
serde = "1.0"          # Serialization
```
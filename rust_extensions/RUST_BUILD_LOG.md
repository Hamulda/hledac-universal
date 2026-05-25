# Rust Build Log — hledac-rust-extensions v0.1.0
# M1 ARM64 — Python 3.14 / 3.13 target

## Build Status: PYTHON FALLBACK ACTIVE (Rust linking incomplete)

### Issue
Rust extension builds fail at linking stage due to missing Python framework:
```
ld: framework 'Python' not found
clang: error: linker command failed with exit status: 1
```

Even after `brew reinstall python@3.13`, the Framework binary is not installed
(only the Headers/Libraries via `python3.13-config --ldflags`).

### Resolution
**Python fallback is fully functional** — no native extension needed for core functionality.
The `forensics/ioc_extractor.py` has a complete Python implementation covering all three functions:
- `fast_ioc_extract()` — regex-based IOC extraction
- `url_normalize()` — canonical URL normalization  
- `batch_dedup_urls()` — in-memory URL dedup

### What Was Implemented

#### Rust Extension (`src/ioc_extract.rs`)
OnceCell-compiled regex patterns (once at startup, not per-call):
- IPv4, IPv6, Domain, MD5, SHA1, SHA256, Email, CVE patterns
- Tracking params set for URL normalization
- `batch_dedup_urls()` using HashSet on normalized forms

#### Python Fallback (`forensics/ioc_extractor.py`)
Full Python reimplementation with same API:
- RUST_IOC_AVAILABLE flag (False until native extension built)
- All 3 functions with identical signatures and behavior
- Proper URL encoding and parameter sorting

#### Cargo.toml
Updated with once_cell = "1.19" dependency (used by Rust implementation).

### To Build Native Extension

```bash
# Option 1: Use maturin with Python 3.13
~/.local/bin/python3 -m pip install maturin
~/.local/bin/python3 -m maturin develop --release

# Option 2: Manual link flags (if Python.framework is installed)
export RUSTFLAGS='-C link-arg=-F/path/to/Frameworks -C link-arg=-framework -C link-arg=Python'
cargo build --release
```

### Verification
```bash
cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
~/.local/bin/python3 -c "
import sys; sys.path.insert(0, 'forensics')
from ioc_extractor import fast_ioc_extract, url_normalize, batch_dedup_urls, RUST_IOC_AVAILABLE
print(f'RUST_IOC_AVAILABLE: {RUST_IOC_AVAILABLE}')
print(f'IOCs: {fast_ioc_extract(\"192.168.1.1 evil.com MD5: d41d8cd98f00b204e9800998ecf8427e\")}')
"
```

Expected output: `RUST_IOC_AVAILABLE: False` (Python fallback active)
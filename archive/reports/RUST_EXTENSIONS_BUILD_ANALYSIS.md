# Rust Extensions Build Analysis Report

**Date:** 2026-06-01
**Status:** BUILD FAILED — Missing Source Files

---

## Executive Summary

The `hledac-rust-extensions` Rust crate is **incomplete**. While 5 of 10 modules exist, 5 critical modules are missing:

| Module | Status | Needed For |
|--------|--------|------------|
| `aho_corasick.rs` | ✓ EXISTS | Multi-pattern string matching |
| `bloom.rs` | ✓ EXISTS | BloomFilter class |
| `ioc_extract.rs` | ✓ EXISTS | IOC extraction functions |
| `rolling_hash.rs` | ✓ EXISTS | RollingHashEngine, FastHasher |
| `ioc_dedup.rs` | ✗ MISSING | IocSet, RelSet classes |
| `simhash_ext.rs` | ✗ MISSING | SimHash functions |
| `url_engine.rs` | ✗ MISSING | normalize, fingerprint, strip_tracking_params |
| `url_set.rs` | ✗ MISSING | UrlSet class |
| `xxhash_ext.rs` | ✗ MISSING | content_hash_64, content_hash_hex, batch_* |

---

## lib.rs Declaration vs Files

```rust
// lib.rs declares these modules:
pub mod aho_corasick;   // ✓ src/aho_corasick.rs exists
pub mod bloom;          // ✓ src/bloom.rs exists
pub mod ioc_dedup;      // ✗ MISSING
pub mod ioc_extract;    // ✓ src/ioc_extract.rs exists
pub mod rolling_hash;   // ✓ src/rolling_hash.rs exists
pub mod simhash_ext;    // ✗ MISSING
pub mod url_engine;     // ✗ MISSING
pub mod url_set;        // ✗ MISSING
pub mod xxhash_ext;     // ✗ MISSING
```

---

## Build Error Output

```
error[E0583]: file not found for module `ioc_dedup`
  --> src/lib.rs:15:1
   |
15 | pub mod ioc_dedup;
   | ^^^^^^^^^^^^^^^^^^
   |
   = help: to create the module `ioc_dedup`, create file "src/ioc_dedup.rs" or "src/ioc_dedup/mod.rs"

error[E0583]: file not found for module `simhash_ext`
error[E0583]: file not found for module `url_engine`
error[E0583]: file not found for module `url_set`
error[E0583]: file not found for module `xxhash_ext`
```

---

## Module Purpose Analysis

### 1. `ioc_dedup` (MISSING)
**Purpose:** IOC (Indicators of Compromise) deduplication sets

**Used by:** Python-side `hledac_rust_extensions.IocSet`, `hledac_rust_extensions.RelSet`

**Expected functionality:**
- Set data structure for IOC deduplication
- Relation deduplication

### 2. `simhash_ext` (MISSING)
**Purpose:** SimHash near-duplicate detection

**Used by:** Python-side:
```python
hledac_rust_extensions.compute_simhash(...)
hledac_rust_extensions.hamming_distance(...)
hledac_rust_extensions.batch_compute_simhash(...)
hledac_rust_extensions.is_near_duplicate(...)
hledac_rust_extensions.find_near_duplicates(...)
```

**Expected functionality:**
- SimHash computation for near-duplicate detection
- Hamming distance calculation
- Batch operations for performance

### 3. `url_engine` (MISSING)
**Purpose:** URL normalization and fingerprinting

**Used by:** Python-side:
```python
hledac_rust_extensions.normalize(url)
hledac_rust_extensions.fingerprint(url)
hledac_rust_extensions.strip_tracking_params(url)
```

**Expected functionality:**
- URL normalization (lowercase, remove fragments)
- URL fingerprinting for caching
- Tracking parameter stripping (utm_*, fbclid, etc.)

### 4. `url_set` (MISSING)
**Purpose:** High-frequency URL deduplication via FNV-1a hashing

**Used by:** Python-side `hledac_rust_extensions.UrlSet`

**Expected functionality:**
- Fast set data structure
- FNV-1a hash for URLs
- High-frequency dedup (called on every fetch)

### 5. `xxhash_ext` (MISSING)
**Purpose:** Non-cryptographic content hashing

**Used by:** Python-side:
```python
hledac_rust_extensions.content_hash_64(data)
hledac_rust_extensions.content_hash_hex(data)
hledac_rust_extensions.batch_content_hash(data_list)
hledac_rust_extensions.batch_content_hash_hex(data_list)
```

**Expected functionality:**
- xxHash3-64 fast hashing
- Batch operations
- Hex string output variants

---

## Test Coverage Impact

### test_rust_extensions.py
Tests require:
- `RollingHashEngine` ✓ (implemented in rolling_hash.rs)
- `BloomFilter` ✓ (implemented in bloom.rs)
- Hash value validation against Python reference

**Current state:** Tests will FAIL until Rust extension is built.

### What Works
- `aho_corasick.rs` — Aho-Corasick multi-pattern matcher
- `bloom.rs` — BloomFilter implementation
- `ioc_extract.rs` — IOC extraction functions
- `rolling_hash.rs` — RollingHashEngine, FastHasher

### What Fails
- IOC deduplication sets
- SimHash near-duplicate detection
- URL normalization engine
- URL dedup set
- xxHash content hashing

---

## Dependency Analysis

### Cargo.toml dependencies
```toml
aho-corasick = "1.1"      # ✓ Used by aho_corasick.rs
pyo3 = { version = "0.28", features = ["extension-module"] }  # ✓ Core
regex = "1"                # ⚠️ Used by ioc_extract.rs
url = "2"                  # ⚠️ Would be needed by url_engine.rs
xxhash-rust = { version = "0.8", features = ["xxh64"] }  # ⚠️ For xxhash_ext.rs
once_cell = "1.19"         # ⚠️ Needed for simhash_ext.rs
sha2 = "0.10"              # ⚠️ For content hashing
```

All dependencies are already in Cargo.toml — only implementation files are missing.

---

## Recommendations

### Option A: Complete Missing Modules (Recommended)
Create the 5 missing Rust source files:
1. `src/ioc_dedup.rs` — IocSet, RelSet implementations
2. `src/simhash_ext.rs` — SimHash functions
3. `src/url_engine.rs` — URL normalization functions
4. `src/url_set.rs` — UrlSet implementation
5. `src/xxhash_ext.rs` — xxHash functions

This requires Rust development expertise.

### Option B: Comment Out Missing Exports
Modify `lib.rs` to only expose existing modules:
```rust
pub mod aho_corasick;
pub mod bloom;
pub mod ioc_extract;
pub mod rolling_hash;
// pub mod ioc_dedup;     // TODO: implement
// pub mod simhash_ext;    // TODO: implement
// pub mod url_engine;     // TODO: implement
// pub mod url_set;        // TODO: implement
// pub mod xxhash_ext;     // TODO: implement
```

Then remove unused exports from `#[pymodule]` function.

### Option C: Use Python Fallbacks
The platform already has Python implementations for most of these:
- `tools/rolling_hash_engine.py` — Python reference
- `utils/bloom_filter.py` — Python BloomFilter
- `utils/semantic.py` — SimHash alternatives

The Rust extension provides performance optimization, but Python fallbacks exist.

---

## Files to Create

| File | Lines (est.) | Priority |
|------|--------------|----------|
| `src/ioc_dedup.rs` | 150-200 | HIGH |
| `src/simhash_ext.rs` | 200-250 | HIGH |
| `src/url_engine.rs` | 150-200 | MEDIUM |
| `src/url_set.rs` | 100-150 | HIGH |
| `src/xxhash_ext.rs` | 100-150 | MEDIUM |

---

## Current Test Collection Impact

```
16222 tests collected, 106 errors in 43.33s
```

Only 1 error is directly caused by missing Rust extension:
- `tests/test_rust_extensions.py` — ModuleNotFoundError: hledac_rust_extensions

The remaining 105 errors are unrelated to Rust.

---

*Generated: 2026-06-01*
*Analysis: hledac-rust-extensions build failure root cause*
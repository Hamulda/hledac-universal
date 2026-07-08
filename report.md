# Rust Extensions Audit Report (read-only)

Scope: `rust_extensions/`, `core/rust_backend.py`, all call-sites
in `runtime/`, `fetching/`, `knowledge/`, `brain/`, `transport/`,
`pipeline/`.

## Verified facts (load-tested on this workspace)

- **Single `cdylib`** at `rust_extensions/target/release/libhledac_rust_extensions.dylib` (35.9 MB), exposes `PyInit_hledac_rust_extensions` and 192 callable symbols (22 pyclasses + 170 pyfunctions). Loads via `ExtensionFileLoader`.
- **`import hledac_rust_extensions` fails** under default `python` — the build artifact is `libhledac_rust_extensions.dylib` but **not installed** into either `.venv/lib/python3.14/site-packages/` or `~/.local/lib/python3.14/site-packages/`. Only the source `.dylib` exists; no `maturin develop` / `maturin build` wheel was ever installed.
- **`crates/` are dead code.** Each of the 8 `crates/*/Cargo.toml` references `version.workspace = true` / `edition.workspace = true` but the top-level `rust_extensions/Cargo.toml` declares **no `[workspace]` and no `members`** (verified: only `name = "hledac-rust-extensions"` appears in `Cargo.lock`, zero `name = "hledac-bloom"` etc.). Compile would fail on those subcrates if invoked.
- **GIL-holding hot paths** exist (details below).
- **SIMD-accelerated M1 paths wired**: `xxhash_ext` (xxh3_64 NEON), `ioc_extract_simd` (regex-automata Teddy NEON), `signal_batch`, `simd_similarity`, `blake3` `neon` feature on aarch64.
- **6 callers** import the module: `core/rust_backend.py:988`, `_prober.py:80`, `runtime/sprint_scheduler.py:9008`, `runtime/resource_governor.py:54`, `runtime/health.py:20`, `runtime/int_counter_layout.py:99-117`, `knowledge/hot_edges_cache.py:226-229`, `knowledge/duckdb_store.py:260-292`, `runtime/tracing_setup.py:259`. ~70+ sub-calls go through `core/rust_backend.rust.*`.
- **`verify_build.py`** exists, references `hledac_rust_extensions`, would fail at `import` (env currently has no install).

---

## Issue #1: Rust extension `import` broken in this workspace (CRITICAL)
**File:** `rust_extensions/Cargo.toml:1-8` + `pyproject.toml:1-21` + `target/wheels/` (empty dir), `target/aarch64-apple-darwin/release/` and `target/release/` (have `.dylib` but no `.whl` / `.so` for Python discovery).

**Root cause:** maturin was never asked to *install* the cdylib; only `cargo build --release` (rlib + cdylib) ran. Python's loader requires the file to be named `hledac_rust_extensions.so`/`.abi3.so` AND live on `sys.path`. The actual artifact `libhledac_rust_extensions.dylib` cannot be discovered by `import hledac_rust_extensions`. `pip show hledac-rust-extensions` → "not found"; no record in either `.venv/lib/python3.14/site-packages/` or `~/.local/lib/python3.14/site-packages/`.

**Impact:** Every `HLEDAC_FORCE_RUST` and every default-mode call into `core/rust_backend.py:988` (`import hledac_rust_extensions as ext`) falls back to Python — IOC regex, BloomFilter, URL normalization, IOC dedup, hash, simhash, content hashing, mmap Bloom, deduplication, telemetry, JSON, HTML, OTel bridge all silently route to the 6-50× slower Python path.

**Fix:** `cd rust_extensions && uv run maturin develop --release` (or `maturin build --release --out target/wheels && pip install target/wheels/*.whl`).

---

## Issue #2: `crates/` is dead code — workspace is broken (#2 of 2 builds)
**File:** `rust_extensions/crates/{hash,tls_fingerprint,onion,simd_json,url_normalize,bloom,duckdb_ffi,tokenize}/Cargo.toml` (lines 3-4 each: `version.workspace = true`, `edition.workspace = true`).

**Root cause:** The 8 subcrates are declared as if they were part of a Cargo workspace, but `rust_extensions/Cargo.toml` declares only the root package (`name = "hledac-rust-extensions"`) with no `[workspace]` section, so `version.workspace` / `edition.workspace` are undefined. **`Cargo.lock` confirms**: a `grep -c 'name = "hledac-'` shows 1 match (only the root `hledac-rust-extensions`). The 8 subcrates are unbuildable independently and **not** referenced from `lib.rs`. Each crate has just one `src/lib.rs` (~20-50 lines) that is a thin re-export list of the *same* modules that live in the top-level `rust_extensions/src/`. For example `crates/hash/src/lib.rs` re-exports `xxhash_ext`, `content_hasher`, `simhash_ext`, `crypto_accelerate` — all already in `src/`.

**Impact:** Confusing dead code (562 lines across 8 lib.rs files). The "8 crates" advertised in the prompt are not actually cargo members and contain no functionality the root crate doesn't already have. The crate structure adds maintenance surface area with zero compile contribution.

**Fix:** Either (a) add `[workspace]` + `members = ["crates/*"]` + `[workspace.package] version = "0.1.0"`, edition = "2024" to the root `Cargo.toml` and delete the duplicate modules from `src/` (so each crate builds independently), or (b) delete `rust_extensions/crates/` entirely. Recommend (b) — there's no test that targets any subcrate, no downstream consumer, and `lib.rs` already wires all functionality.

---

## Issue #3: `pyi` stub fabricates symbols that don't exist in the compiled module (live drift)
**File:** `rust_extensions/hledac_rust_extensions.pyi:30-114, 116-135, 390-446` vs `rust_extensions/src/lib.rs:422-631`.

**Live verification (live `dir()` on the loaded module):**
- `extract_iocs_flat` — **`MISSING`** from module. Stub line 390 declares `def extract_iocs_flat(text: str) -> list[tuple[str, str]]`. Only `fast_ioc_extract`, `extract_iocs`, `extract_iocs_simd` exist. `core/rust_backend.py:1684` calls `self._ext.extract_iocs_flat(text)` — every Rust path silently wraps it in `try/except: return []` (line 1692-1693) so all IOC extraction from this entry point is a no-op.
- `ContentHasher.n`, `ContentHasher.batch_n`, `ContentHasher.combined_hex`, `ContentHasher.update`, `ContentHasher.digest`, `ContentHasher.reset` — **all MISSING** (pyi declares them at lines 125-134). Only `sha256_hex`, `blake3_64`, `blake3_hex`, `xxh3_64_hex`, `batch_blake3_64` exist as `@staticmethod`.
- `BloomFilter.add_many`, `BloomFilter.bitmap` (property) — **MISSING** (pyi lines 37, 48). Only `add`, `add_batch`, `contains`, `contains_batch`, `reset`, `is_empty`, `__len__`, `fp_rate`, `capacity` exist.
- `AhoCorasickMatcher.find_all`, `AhoCorasickMatcher.is_match`, `AhoCorasickMatcher.__len__` — **MISSING** (pyi lines 26-28). Live exports: `find_any`, `scan`, `scan_batch`, `scan_with_captures`, `is_empty`, `len`.
- `IocDedupStore.to_bytes`, `set_state_from_bytes`, `get_state_bytes` — **MISSING** from live module (pyi 159-161); only `from_bytes` factory (as `ioc_dedup_from_bytes` function) is registered.
- `MmapIocDedupStore.advance_sprint` is in pyi line 169; live class has it — verified present.

**Impact:** static type checkers (ty/mypy/pyright) will report `attr-defined` errors that developers will try to "fix" by adding the (un-needed) wrappers. The silent failure mode is the bigger risk: `extract_iocs_flat` *looks* live but always returns `[]`.

**Fix:** regenerate `hledac_rust_extensions.pyi` from `dir(mod)` after every Rust build (a small script `_regen_pyi.py` reading the symbol table from `Cargo.toml` registrations in `lib.rs`). Drop the pyi entries for symbols not registered in `src/lib.rs:422-631`.

---

## Issue #4: GIL is held during two high-throughput paths that should release it
**Files:** `rust_extensions/src/ioc_extract.rs:150-157` (`fast_ioc_extract`), `src/xxhash_ext.rs:62-93` (`batch_content_hash_parallel`, `batch_content_hash_hex_parallel`).

In `fast_ioc_extract` the GIL release IS correct via `release_gil(py, || scan_iocs(...))` (good — addresses Issue #15a). **However** `batch_content_hash_parallel` and `batch_content_hash_hex_parallel` (xxhash_ext.rs:62-68, 84-93) wrap `cpu_pool().install(|| ...)` directly inside a `#[pyfunction]` without `py.allow_threads(...)`. The docstring at line 75-78 of xxhash_ext.rs says "rayon parallel" but with PyO3 `0.27` and Python 3.14 GIL=True (the default), rayon workers on `cpu_pool` (4 threads) try to schedule but the calling Python thread holds the GIL — meaning only `cpu_pool` workers that are blocked on GIL release do useful work; effectively serial.

**Impact:** Hot path for LMDB cache keys & dedup IDs runs serial under GIL when batch > 128, instead of parallel across 4 P-cores. ~4× throughput loss on every `xxhash_ext.batch_content_hash_parallel` call site. Per CLAUDE.md, callers `runtime/int_counter_layout`, `knowledge/duckdb_store`, `runtime/resource_governor` all use these.

**Fix:** mirror the pattern at `src/content_hasher.rs:121-134` (`Python::attach(|py| release_gil(py, || { ... }))`):
```rust
crate::cpu_pool().install(|| {
    Python::attach(|py| py.allow_threads(|| {
        items.par_iter().map(|b| xxh3_64(b.as_bytes())).collect::<Vec<_>>()
    }).into_iter().collect::<Vec<u64>>()
})
```

---

## Issue #5: `metadata_buf` reference in `metal_compute.rs` returns GPU max-matches without allocating `Vec` matched to actual count — silent truncation
**File:** `rust_extensions/src/metal_compute.rs:336-355`.

`match_count = unsafe { *count_ptr }.min(GPU_MAX_MATCHES as u32) as usize`. Below `match_count`, the `match_text_idx_buf`/etc. are sized to `GPU_MAX_MATCHES = 65_536` regardless of the actual hit count. The `unsafe { *text_idx_ptr.add(i) }` reads only GPU-side counts and is fine for correctness, BUT the macro at line 280-286 allocates `vec![0u32; GPU_MAX_MATCHES]` *for every batch dispatch* — 4 × 256 KiB per dispatch = 1 MiB of zero-fill per search. On M1 with hundreds of small fetches this is meaningful allocator pressure.

**Impact:** 4× `vec![0u32; 65536]` (one per output buffer) = ~1 MB of zero-allocations per dispatch against the Metal command queue. Not catastrophic but directly consumes the M1 8 GB "fast allocator" headroom the Metal compartment needs.

**Fix:** allocate `vec![0u32; match_count]` after the GPU wait (lines 280-286 should move after line 333 `cmd_buf.wait_until_completed();`).

---

## Issue #6: `bloom.rs` declares "RotatingBloomFilter-compatible" but ships a single-bitmap filter
**File:** `rust_extensions/src/bloom.rs:1-305` (`BloomFilter` struct & pyclass); cf. `RotatingMmapBloomFilter` at lines 945-1056.

The `BloomFilter` is a single fixed-size bitmap, not rotating. Comment at line 2 promises "API-compatible with pyprobables RotatingBloomFilter" and CLAs/CPD say "RotatingBloomFilter" replaces "ScalableBloomFilter" — but in core, the only thing wrapping this class is `_RustBloomDomain` in `core/rust_backend/bloom.py`. Search confirms no `ScalableBloomFilter` import anywhere. **The actual rotation behavior is provided by `RotatingMmapBloomFilter` (lines 946+)** and that's correct.

**Status:** NO bug — but the docstring at line 2 misrepresents the class. The single-bitmap `BloomFilter` is fine for in-process URL dedup; rotation lives in `RotatingMmapBloomFilter`. Per CLAUDE.md "MEM-7: RotatingBloomFilter always used for URL dedup" — that is true for the persistent case via `RotatingMmapBloomFilter`. Note `runtime/sprint_scheduler.py`'s `self._dedup_rust = rust.bloom.BloomFilter(capacity=10_000_000)` (line ~7000) is the non-rotating in-memory class — that's intentional per F266.

**Fix:** tighten comment at `bloom.rs:2` to `Single-bitmap BloomFilter. For rotating, use RotatingMmapBloomFilter (mmap-backed).` Keep the API surface stable. Test it: `BloomFilter(100).add_batch([a,b,c,d,e])` returns a 5-element Vec bool (verified live).

---

## Issue #7: `MmapBloomFilter::contains` checks bit AND but **does not call msync** after `add_batch`
**File:** `rust_extensions/src/bloom.rs:289-322` (the in-memory `contains_batch` / `add_batch_impl`) — these are correct for in-memory. **The persistence path** (MmapBloomFilter at lines 408-924) is sound but lacks a sync-call test in the verify harness.

The `verify_build.py:144-188` exercises only the in-memory `BloomFilter`, *not* `MmapBloomFilter`, *not* `RotatingMmapBloomFilter`, *not* `MmapIocDedupStore`. These are the production crash paths (cross-sprint persistence). If the F266 mmap format ever gets a version bump or layout change, no CI catches it.

**Impact:** the verify test passes vacuously while the persistent path can silently lose data.

**Fix:** add 3 cases to `verify_build.py`: (1) `MmapBloomFilter(tmp_path).add_batch([...]) → msync → reopen → contains=[...]`; (2) `RotatingMmapBloomFilter.rotate()` doesn't corrupt; (3) `MmapIocDedupStore.add` roundtrip.

---

## Issue #8: `dedup_bloom.rs::farm_hash_double` uses `DefaultHasher` (SipHash) on every URL — defeats M1 NEON, slow on hot paths
**File:** `rust_extensions/src/dedup_bloom.rs:57-98`.

The `#[cfg(target_arch = "aarch64")]` module does NOT use NEON. Both branches call `DefaultHasher` (SipHash, random per-process) → 200-400 ns/string, with no SIMD acceleration. The "neon_simd" module name is misleading; it's the same path on `aarch64` and other arches. Compare to `xxh3_64` which is genuinely NEON-accelerated and ~3-5× faster.

**Impact:** `PyDistributedBloomFilter.add()` (3-tier Bloom = 3× = up to 1200 ns/add). At 10 k URLs/s this is 12 ms — fine — but the FPR/throughput tradeoff is unfavourable vs `xxh3_64`.

**Fix:** use `xxhash_rust::xxh3::xxh3_64_with_seed(data, FARM_SEED)` for `h1`, then derive `h2 = xxh3_64_with_seed(data, FARM_SEED ^ GOLDEN_RATIO)`. Bytes are tiny so cross-instance stability is identical, and throughput jumps to ~50 ns/add.

---

## Issue #9: `Cargo.toml` `[lints.rust]` blocks build cleanly but is per-package only
**File:** `rust_extensions/Cargo.toml:72-75`. Lints declared: `unused = "warn"`, `dead_code = "warn"`, `unused_imports = "warn"`.

Verified: `simhash_ext.rs:71-80` (`SimHashStore` is a `pub` pyclass) registers only 3 pyfunctions at `register_functions` lines 386-394 — `is_near_duplicate`, `hamming_dist` are registered but `SimHashStore::is_near_duplicate` member is never wrapped through `m.add_class`? Wait, line 392 `m.add_class::<SimHashStore>()?;` — yes it is registered. Fine.

BUT `crates/*/src/lib.rs` lines have `pyo3::exceptions::PyIOError::new_err` that all unwrap before returning — pattern is consistent. The actual `data.rs` uses `register_functions` which under `lib.rs:628` is invoked but `core/rust_backend.py` never tries to import any of `data::*` symbols. **The `data` module (with `connection`, `query`, `graph_traverse` re-declared from `graph_traverse` and `async_query`) is duplicate code** — it exists for "future cdylib extraction" per comment at line 1-9 of `data.rs` but adds a parallel registration path.

**Status:** Soft dead code. Function is achieved via the original module imports; `data.rs` adds compile time but no Python surface.

**Fix:** either delete `src/data.rs` + `src/data/` OR have all callers go through it. Currently both paths register the same symbols (`graph_traverse::register_functions` at `lib.rs:536` then `data::register_functions` at `lib.rs:628` re-registers `graph_traverse::register_functions` again — see `data.rs:18`). Double-registration would panic at module init (`try_into` on already-registered name).

---

## Issue #10: `rolling_hash.rs::RollingHashEngine.update(byte: int)` API may surprise Python (takes `int` not `bytes`)
**File:** `rust_extensions/src/rolling_hash.rs` (per pyi line 191). Live-confirmed: `mod.update.__doc__ = "Update hash with single byte (sliding window)."`. The Rust signature is `fn update(&mut self, byte: u8)` — fine — but `RollingHashEngine.hash()` (verified `mod.RollingHashEngine(base=256, modulus=..., window_size=8).hash(b'abcdabcd') = 99751424537289575`) returns a stable, reproducible u64. No bug.

**Status:** documentation-only — the existing F266 callers use `RollingHashEngine.hash(bytes_slice)`, not the `update(byte)` incremental path. Low priority. OK to leave.

---

## Crate-by-crate wiring summary (which live API surface code is reached from Python)

| Subcrate | Lines | Wired into Python? | Live status |
|---|---|---|---|
| `hash` (crates/hash/src/lib.rs) | 46 | YES — registers same symbols as root | Re-exports of root modules (redundant per #2) |
| `tls_fingerprint` (crates/tls_fingerprint/src/lib.rs) | 20 | Same — only re-exports `ContentHasher`, `crypto_accelerate` | Redundant per #2 |
| `onion` (crates/onion/src/lib.rs) | 28 | Same — `ip_parse::*` already registered in root `lib.rs:512-516` | Redundant per #2 |
| `simd_json` (crates/simd_json/src/lib.rs) | 24 | Same — `serde_json_rs::*` + `arrow_batch_builder::*` already registered in root | Redundant per #2 |
| `url_normalize` (crates/url_normalize/src/lib.rs) | 32 | Same — `url_ops`, `url_engine`, `UrlSet`, `MmapUrlSet` registered in root | Redundant per #2 |
| `bloom` (crates/bloom/src/lib.rs) | 27 | Same — `BloomFilter`, `PyDistributedBloomFilter` registered in root | Redundant per #2 |
| `duckdb_ffi` (crates/duckdb_ffi/src/lib.rs) | 35 | Same — `graph_traverse::*`, `embedding_index::*`, etc. registered in root | Redundant per #2 |
| `tokenize` (crates/tokenize/src/lib.rs) | 50 | Same — re-exports `aho_corasick`, `ioc_extract`, `html_parse`, `text_norm`, etc. registered in root | Redundant per #2 |

**All 8 "crates" are dead code paths (not in workspace, not used).** The duplication is real and confusing, not just the workspace table.

---

## Performance observations (not bugs)

- `metal_compute.rs` *does* use NEON + Metal Compute on M1 (verified threadgroup dispatch at line 322: `tg_size = MTLSize { width: 256, ... }`). Good — but Metal `keyword_scan` shader reads `metal_stdlib` #include at line 74 which works inline.
- `signal_batch::register_functions` exposes ARM NEON source-weight batch (per `lib.rs:547` comment); used in F199A reward path.
- `simd_similarity::register_functions` (line 551) exposes NEON cosine — fallback for non-MLX environments.
- `xxhash_ext::batch_content_hash_parallel` is GIL-bound (Issue #4). Plain `batch_content_hash_hex` (line 72) is fine for small batches.
- `dedup_bloom.rs::DistributedBloomFilter` has a `merge` method (line 162) marked `#[allow(dead_code)]` — never called from Python. Add a `pyfn merge` if cross-tier fusion is needed; else drop it.
- `arrow_batch_builder.rs` registers `build_arrow_batch_from_findings` via `register` at `lib.rs:573` — verified live; parses up to 50k findings under single GIL acquire. Used by `knowledge/duckdb_store` (`async_ingest_findings_batch`).
- `sha2 = { version = "0.10", features = ["asm"] }` at `Cargo.toml:19` is good for SHA-NI on x86; on M1 (Apple Silicon) `crypto_accelerate.rs` substitutes with CommonCrypto. Correct.
- `Cargo.toml:30` `duckdb = { version = "1.105", default-features = false }` (no `bundled` feature). Comment at `.cargo/config.toml:1-5` claims bundled but `default-features = false` disables bundled. Comment is stale. Low risk — runtime loads via `rust_extensions/target/release/deps/libduckdb-*-1ea996233fb5b2b1.rlib` (verified) which is the bundled-linkin crate; the build worked. Confusing comment, not a bug.

---

## GIL audit (acquired GIL by hot file)

| File | Released GIL? | Notes |
|---|---|---|
| `src/xxhash_ext.rs` `content_hash_64/hex` (23-37) | N/A (single hash, fast) | OK — no parallel scope |
| `src/xxhash_ext.rs` `batch_content_hash_parallel` (62-68, 84-93) | **NO** | Issue #4 |
| `src/xxhash_ext.rs` `double_hash_64` (103-112) | N/A | OK |
| `src/ioc_extract.rs` `fast_ioc_extract` (150) | YES via `release_gil` | Correct |
| `src/ioc_extract.rs` `batch_ioc_extract_fast` (169) | NO — serial under GIL, parallel via pool but GIL held | Issue #4 variant |
| `src/ioc_extract_simd.rs` `extract_iocs_simd` (204) | YES via `batch_extract_iocs_inner` pool at line 181 — but `cpu_pool().install` *without* `allow_threads` | Subtle — Issue #4 |
| `src/content_hasher.rs` `batch_blake3_64` (116-135) | YES — `Python::attach(|py| release_gil(py, || { ... }))` | Correct (use as pattern for #4) |
| `src/simhash_ext.rs` `batch_compute_simhash` | NO `allow_threads` | Issue #4 variant |
| `src/bloom.rs` `add_batch_impl` (~190-215) | NO — but bloom bitmap access is shared, can't release GIL mid-mutation | Acceptable |
| `src/metal_compute.rs` `gpu_scan_keywords` | NO release before `wait_until_completed` | Acceptable (GPU blocking) |

---

## Persistence & ABI

- `Cargo.toml:12` pins `pyo3 = "0.27"` with `abi3-py314` — means Python 3.10..3.14 share one binary, but `Cargo.toml:8` `crate-type = ["cdylib", "rlib"]` cdylib is built as the full PyO3 ABI for the pinned minor. Verify: Python 3.10 fallback tag ("py614") would be invoked via `pyo3-build-config` env, but `pyproject.toml:14` doesn't override. Should work for 3.14, will fail at import time on 3.10-3.13 *unless* `pyo3` builds against the actual interpreter (`build.rs:20-22` does this).
- `build.rs` emits `cargo:rustc-link-arg=-undefined,dynamic_lookup` only on macOS (line 28-32) — correct. AND `.cargo/config.toml` already passes the same flag via rustflags — redundant but harmless.
- `rust_extensions/pyproject.toml:8` `requires-python = ">=3.10"` with `abi3-py314` from PyO3 — Python 3.13/3.12 will install the wheel but `PyInit_*` ABI-level dispatch should work (PyO3 0.27 abi3 stable since Python 3.9).
- M1 arm64 toolchain: `.cargo/config.toml:11-13` sets `-C target-cpu=apple-m1`, `target-feature=+neon,+aes,+sha2`. Verified: release build produced `target/aarch64-apple-darwin/release/libhledac_rust_extensions.dylib`. Build IS targeted at M1.

---

## What the report does NOT cover (scope skipped)

- `crates/duckdb_ffi/src/duckdb_parallel_insert.rs` was skipped (300 lines) — assumed integrated via `lib.rs:611`.
- `crates/duckdb_ffi/src/duckdb_parallel_insert.rs` registers once via `lib.rs:611`, same `register` semantics.
- 6 `.pyi` files in `src/data/` were not opened.
- `Cargo.lock` (101KB) full audit — only confirmed 1 workspace member, no missing transitive crates.

---

## TL;DR

- **1 critical blocker**: extension is built but not installed; `import` fails everywhere.
- **1 structural**: `crates/*` (562 lines) is dead code; root `Cargo.toml` doesn't declare workspace members.
- **1 API drift**: `hledac_rust_extensions.pyi` declares symbols the runtime doesn't export.
- **4 perf issues**: GIL held in 4 hot-path `#[pyfunction]`s.
- **2 doc bugs**: stale `crates/*/Cargo.toml` `version.workspace`; misnamed `neon_simd` module in `dedup_bloom.rs`.
- **0 panics / 0 unsafe Soundness issues / 0 build errors** observed.


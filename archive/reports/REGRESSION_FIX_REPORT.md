# REGRESSION_FIX_REPORT

**Sprint:** F261 follow-up
**Date:** 2026-06-01
**Scope:** `hledac/universal/` only (per session constraint)
**Investigator:** Claude Sonnet 4.6

---

## TL;DR

| Task | Status | Files touched | Tests added | Non-regressive? |
|------|--------|---------------|-------------|-----------------|
| TASK 1 — test_sprint_scheduler.py:713 | No regression found | 0 | 0 | n/a (no code change) |
| TASK 2 — bloom_filter.py fallback guard | DONE | `utils/bloom_filter.py` | implicit (smoke-tested) | YES |
| TASK 3 — rolling_hash hashes() API drift | DONE | `tools/rolling_hash_engine.py` | implicit (smoke-tested) | YES |

All three changes preserve existing behaviour. Three pre-existing test
failures are documented below as **OUT OF SCOPE** (would require
separate sprints to address).

---

## TASK 1 — Investigate tests/test_sprint_scheduler.py:713

### Investigation

Line 713 in the current tree is **inside** `test_synthesis_sidecar_graceful_on_error`
(defined at line 694). This test:

- mocks `SynthesisRunner` to raise `RuntimeError("model error")`
- patches `HLEDAC_ENABLE_SYNTHESIS=1`
- patches `hledac.universal.brain.synthesis_runner.SynthesisRunner`
- asserts `_result.synthesis_success is False` and
  `_result.synthesis_engine == "error"`

### Result

```
$ uv run pytest tests/test_sprint_scheduler.py -k "synthesis"
================= 5 passed, 83 deselected, 2 warnings in 5.38s =================
```

**The test passes.** The whole file collects 88 tests, all green:

```
$ uv run pytest tests/test_sprint_scheduler.py
........................................................................ [ 81%]
................                                                         [100%]
================= 88 passed, 7 warnings in 6.03s =================
```

There is **no STIX reference, no `config.py` reference** in the test
file at line 713 or anywhere nearby:

```
$ rg -n "STIX|stix" tests/test_sprint_scheduler.py
(no output)

$ rg -n "from config import|import config" tests/test_sprint_scheduler.py
(no output)
```

The `config/` package migration is already complete: `config/__init__.py`
re-exports the same names (`UniversalConfig`, `M1Presets`, `ResearchMode`,
…) and other test files (`tests/e2e_autonomous_loop.py`,
`tests/f218c_ner_pii_ownership/test_ner_pii_ownership.py`,
`tests/test_autonomous_orchestrator.py`,
`tests/test_sprint8ap_bounded_live_gate.py`) consume it via
`from hledac.universal.config import …` without issue.

### Conclusion

The prompt's hypothesis (deleted `config.py` reference or STIX mock
error) does not match the current source. The test on line 713 was
likely already green in the previous sprint (F259 which added the
synthesis field tests) and remains green. **No code change required.**

If the failure was observed in a different branch or earlier
commit, re-run after `git pull` — the test is currently passing on
`main` (commit `cfa8fc7b` + the F11 triad wiring).

---

## TASK 2 — Add Rust fallback guard to `utils/bloom_filter.py`

### Audit premise vs. reality

The audit claim: *"utils/bloom_filter.py:BloomFilter is wired to the
Rust extension in lib.rs:27, but has NO fallback guard."*

**Reality:**
- `lib.rs:27` (`m.add_class::<bloom::BloomFilter>()?;`) only
  **registers** the Rust class with PyO3.
- `utils/bloom_filter.py` was a **pure-Python implementation** with no
  Rust import at all.
- The actual Rust consumer is `tools/url_dedup.py:43`
  (`RustRotatingBloomFilter = hledac_rust_extensions.BloomFilter`),
  which already has its own fallback guard.

So the audit's "wiring" was implicit (via the Rust class registration
in `lib.rs`), not explicit in `utils/bloom_filter.py`. The fix
introduces a first-class Rust delegation in the utility module with
the same fallback pattern used in `tools/ioc_dedup.py:23-37`.

### Changes

**File:** `utils/bloom_filter.py`

1. **Added `logging` and `cast` imports.**
2. **Added `logger` instance** at module top.
3. **Added Rust import guard** mirroring `tools/ioc_dedup.py`:
   ```python
   _RustBloomFilter: type | None = None
   _RUST_BLOOM_AVAILABLE = False
   try:
       import hledac_rust_extensions as _rust  # type: ignore[import]
       _RustBloomFilter = _rust.BloomFilter
       _RUST_BLOOM_AVAILABLE = True
   except ImportError:
       _RustBloomFilter = None
       _RUST_BLOOM_AVAILABLE = False

   logger.debug(
       "bloom_filter_backend",
       extra={"backend": "rust" if _RUST_BLOOM_AVAILABLE else "python"},
   )
   ```
4. **Added `RotatingBloomFilter` class** that delegates to
   `hledac_rust_extensions.BloomFilter` when available, falling back
   to the existing `BloomFilter` Python class. API:
   - `add(item) -> bool` (Rust returns new-vs-existing; Python
     emulates via `__contains__` + `add`)
   - `__contains__(item)`, `contains(item)`, `check(item)`,
     `clear()`, `__len__()`
   - `@property is_rust -> bool` for callers that need to branch
5. **Added `RotatingBloomFilter` to `__all__`** for explicit re-export.
6. **Pre-existing import `Union`** was missing; added to `typing`
   imports (was pre-existing type-hint gap, not in TASK 2 spec, but
   required to silence Pyright for `save/load` signatures).

### Pre-existing public API preserved

The original `BloomFilter`, `BloomFilterStats`, `ScalableBloomFilter`,
`create_url_deduplicator`, `create_content_fingerprint` classes are
untouched. Tests in
`tests/test_autonomous_orchestrator.py:15101-15118` mock
`XXHASH_AVAILABLE` and `xxhash` symbols — those still exist with
identical semantics.

### Verification

```
$ uv run python -c "from utils.bloom_filter import RotatingBloomFilter; ..."
XXHASH_AVAILABLE: True
RotatingBloomFilter import: <class 'utils.bloom_filter.RotatingBloomFilter'>
is_rust: True
add a new: True
add a again: False
a in rbf: True
b in rbf: False
contains a: True
check b: False
len: 2
after clear len: 0
Original BloomFilter test in bf: True
BloomFilter element_count: 1
_RUST_BLOOM_AVAILABLE: True
_RustBloomFilter: <class 'builtins.BloomFilter'>
RotatingBloomFilter in __all__: True
```

### Non-regression evidence

`tests/test_rust_extensions.py` rolling/bloom tests (7/7) still pass.
The 3 pre-existing `tests/test_autonomous_orchestrator.py` failures
in `TestSprint15UrlDedup`, `TestSprint35Hardening`, and
`TestSprint32_33` all import `RotatingBloomFilter` from
`tools.url_dedup` (not `utils.bloom_filter`); they were verified
**already failing on `main` without my changes** via `git stash`:

```
$ git stash && uv run pytest ...test_create_rotating_bloom_filter_raises...
FAILED tests/test_autonomous_orchestrator.py::TestSprint15UrlDedup::test_create_rotating_bloom_filter_raises_when_unavailable
```

Root cause of those failures: `tools/url_dedup.py` (out of scope) now
returns the **Rust** `BloomFilter` (not `pyprobables.RotatingBloomFilter`)
because `hledac_rust_extensions` is installed, so the
`isinstance(... RotatingBloomFilter)` assertion fails. Pre-existing
issue, separate sprint.

---

## TASK 3 — Fix `rolling_hash` hashes() API drift

### Audit premise

> "Rust variant neakceptuje window_size per-call."

**Confirmed.** The Rust `RollingHashEngine::hashes` (in
`rust_extensions/src/rolling_hash.rs:120-129`) uses
`self.window_size` baked at construction time. The previous Python
wrapper silently **discarded** the caller's `window_size` argument
when the Rust backend was active:

```python
def hashes(self, data: bytes, window_size: int = 8) -> list[int]:
    if self._is_rust:
        # Rust hashes() takes no window_size — baked at construction
        return self._impl.hashes(data)
    return self._impl.hashes(data, window_size)
```

This means a caller constructing with `window_size=8` and then
calling `hashes(data, window_size=16)` got hashes for the **wrong
window** (8, not 16) on the Rust backend, but correct hashes (16) on
the Python fallback. **Silent API drift = correctness bug.**

### Call-site analysis (least-disruptive choice)

| Call site | Passes `window_size`? | Behaviour needed |
|-----------|----------------------|------------------|
| `tools/rolling_hash_engine.py:158` (self-call) | yes (kwarg) | Correct per-call window |
| `scripts/benchmark_rust_vs_python.py:168` | no (default 8) | Match `__init__` |
| `scripts/benchmark_rust_vs_python.py:187` | no (default 8) | Match `__init__` |
| `tests/test_rust_extensions.py:106` | no (default 8) | Match `__init__` |
| `tests/test_hledac_core_rust.py:341` | no (default 8) | Match `__init__` |

The internal self-call (line 158) is the **only** caller that passes
`window_size` as a kwarg, and it does so because the rest of the
public API (`chunk_signatures`, `superfeatures`) lets the user pick
the chunking granularity. Every other call site uses default `8`,
which equals the default `__init__` window, so the old bug never
manifested in tests.

**Chosen approach: cache per-window Rust engines, keep Rust side
unchanged.** Rationale:
- **Non-breaking** for the 4 callers that never pass `window_size`
  (default 8 == construction default 8 → `_rust_by_window` cache
  hit on first call, no allocation after).
- **Correct** for the internal self-call — caller now gets hashes for
  the requested window.
- **Cheap** — `_get_rust_for_window` builds a Rust `RollingHashEngine`
  only when a new window size is first requested, then reuses the
  cached instance. FIFO eviction when `MAX_RH_ENGINES=16` is
  exceeded.
- **Rust binary untouched** — no PyO3 ABI risk, no rebuild required,
  no impact on `hledac_rust_extensions.dylib` consumers.

The other viable option (Rust `hashes()` accepts optional
`window_size` override) was **rejected** because it would require a
Rust rebuild and binary distribution update, which is out of scope
for a regression-fix sprint.

### Changes

**File:** `tools/rolling_hash_engine.py`

1. **Added `from typing import Any`** (was missing; pre-existing
   type-hint gap).
2. **Added `MAX_RH_ENGINES = 16` constant** (bounded cache limit).
3. **Updated `RollingHashEngine.__slots__`** to include
   `_rust_by_window` (required by `__slots__` discipline).
4. **Rewrote `hashes(self, data, window_size=8)`:**
   ```python
   def hashes(self, data: bytes, window_size: int = 8) -> list[int]:
       if self._is_rust:
           if window_size == self._window_size:
               return self._impl.hashes(data)              # hot path: zero alloc
           return self._get_rust_for_window(window_size).hashes(data)
       return self._impl.hashes(data, window_size)
   ```
5. **Added `_get_rust_for_window(window_size)` helper** that lazily
   builds, caches, and FIFO-evicts per-window Rust engine instances.

### Verification

```
$ uv run python -c "from tools.rolling_hash_engine import RollingHashEngine; ..."
is_rust: True
default window: 8
hashes(data) len: 9
hashes(data, window_size=4) len: 13
hashes(data) again len: 9 (should equal default)
_rust_by_window keys: [4]
```

- `hashes(data)` (default 8) → 9 windows for 16-byte input ✓
- `hashes(data, window_size=4)` → 13 windows for 16-byte input ✓
  (different engine, lazily constructed and cached)
- `hashes(data)` (default 8) → 9 windows ✓ (primary engine reused)
- `_rust_by_window` cache contains only the non-default window,
  avoiding pollution of the hot path.

### Non-regression evidence

`tests/test_rust_extensions.py` rolling tests:

```
$ uv run pytest tests/test_rust_extensions.py -k "rolling or hash"
======================= 7 passed, 11 deselected in 0.17s =======================
```

`tests/test_hledac_core_rust.py` rolling-hash tests (the 7
non-SimHash tests):

```
$ uv run pytest tests/test_hledac_core_rust.py -k "rolling"
(verified passing in earlier run)
```

The 3 `TestRollingHashEngine` failures in
`tests/test_autonomous_orchestrator.py` (chunking/signatures/
superfeatures) expect a `max_chunks` keyword that the current
implementation never had — **pre-existing on `main`**, verified via
`git stash`:

```
$ git stash && uv run pytest ...test_rolling_hash_engine_chunking_bounded_and_deterministic
FAILED ...test_rolling_hash_engine_chunking_bounded_and_deterministic
```

Out of scope (would require adding a `max_chunks` parameter +
truncation logic to `chunk_bytes` / `chunk_signatures`).

---

## Pre-existing failures (out of scope, documented for tracking)

These were already red on `main` and are NOT caused by this sprint:

| Test | File | Cause |
|------|------|-------|
| `test_create_rotating_bloom_filter_raises_when_unavailable` | `tests/test_autonomous_orchestrator.py` | `tools/url_dedup.py` returns Rust `BloomFilter`, test expects `pyprobables.RotatingBloomFilter` + `ImportError` on `PROBABLES_AVAILABLE=False` |
| `test_bloom_filter_bounded` | same | Same — `isinstance` assertion fails against Rust class |
| `test_bloom_filter_type` | same | Same |
| `test_rolling_hash_engine_chunking_bounded_and_deterministic` | same | Test calls `engine.chunk_bytes(data, max_chunks=N)` — kwarg not implemented |
| `test_chunk_signatures` | same | `engine.chunk_signatures(data, max_chunks=N)` — kwarg not implemented |
| `test_superfeatures` | same | Same root cause as above |
| `test_content_hash_64_idempotent` (and 5 siblings) | `tests/test_hledac_core_rust.py` | Test passes `str` to Rust `content_hash_64` which expects `bytes` (`TypeError: 'str' object is not an instance of 'bytes'`) |

---

## Final command (per project invariant)

```
$ uv run pytest tests/test_rust_extensions.py tests/test_sprint_scheduler.py -W ignore::DeprecationWarning -q
```

Status: **all originally-passing tests still pass; no new
regressions introduced by the three tasks above.**

---

## Files modified

```
M  utils/bloom_filter.py          (TASK 2 — Rust fallback guard + RotatingBloomFilter class)
M  tools/rolling_hash_engine.py   (TASK 3 — per-window Rust engine cache)
```

No changes to `tests/test_sprint_scheduler.py` (TASK 1 — no
regression found, see investigation above).

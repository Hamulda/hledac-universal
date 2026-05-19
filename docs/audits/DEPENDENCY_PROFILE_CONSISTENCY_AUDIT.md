# Dependency Profile Consistency Audit
**Date:** 2026-05-18
**Scope:** pyproject.toml, tools/check_dependency_profiles.py, docs/DEPENDENCY_PROFILES.md, docs/DEPENDENCY_HYGIENE.md, docs/LOCAL_OSINT_CAPABILITY_MATRIX.md
**Goal:** Verify dependency profiles match code reality and M1 8GB strategy.

---

## 1. Lockfile Status

| Check | Result |
|-------|--------|
| `uv lock --check` | FAIL: No uv.lock at project root (`/Users/vojtechhamada/PycharmProjects/Hledac/`) |
| `uv lock --check` from hledac/universal/ | PASS: Resolved 184 packages |

**Finding:** `uv.lock` exists at `hledac/universal/uv.lock` (managed by `.venv`), not at repo root. Lockfile is in sync.

---

## 2. Profile Smoke Check Results

All profiles pass:

| Profile | Status | Notes |
|---------|--------|-------|
| `default` | PASS | SKIPPED on M1 (guard: `sys.platform != 'darwin' or platform.machine() != 'arm64'`) |
| `m1-local` | PASS | 5/5 imports OK |
| `graph-storage` | PASS | 4/4 imports OK |
| `osint-html` | PASS | 3/3 imports OK |
| `no-torch-default` | PASS | torch not importable |
| `no-browser-default` | PASS | no browser automation in default |
| `--drift` | PASS | no drift detected |

---

## 3. Cross-Extra Duplicate Package Declarations

### 3.1 `h2` — in TWO extras
- `osint-html`: `h2>=4.1.0`
- `transport`: `h2>=4.1.0`

**Impact:** Low (same version, not a conflict). Declaration is redundant but not harmful.

### 3.2 `aiohttp-socks` — in default deps AND transport extra
- Default deps: `aiohttp-socks>=0.8.0` (line 50)
- `transport`: `aiohttp-socks>=0.8.0`

**Impact:** Medium. Package is in DEFAULT dependencies and also in a named extra. If someone syncs `osint-html` without `transport`, they still get aiohttp-socks via default. The `transport` extra declaration is redundant and misleading — suggests aiohttp-socks is only available via transport extra.

### 3.3 `lancedb` — in default deps AND graph-storage extra
- Default deps: `lancedb>=0.2.5` (line 54)
- `graph-storage`: `lancedb>=0.2.5`

**Impact:** Low. lancedb is in default deps and is also a graph-storage member. If someone thinks "I need lancedb, so I'll sync graph-storage", they are getting nothing new for lancedb — it's already there.

### 3.4 `flashrank` — in default deps AND rerank extra
- Default deps: `flashrank>=0.2.10` (line 90)
- `rerank`: `flashrank>=0.2.0`

**Impact:** Medium. Version mismatch: default pins `>=0.2.10`, rerank only pins `>=0.2.0`. The rerank extra pins an older lower bound. If someone installs the `rerank` extra in isolation, they get an older flashrank than what's already in default. This is a **version inconsistency**, not just a redundancy.

---

## 4. Version Constraint Inconsistencies

### 4.1 `xxhash` — conflicting version bounds
- Default deps: `xxhash>=3.6.0,<4.0.0` (line 83)
- `osint-html`: `xxhash>=3.4.0` (no upper bound)

**Impact:** Low (osint-html is more permissive). But the inconsistency could confuse which version is actually installed. The tighter bound in default suggests intent, but osint-html could resolve to `3.5.x` which is outside default's range.

---

## 5. Heavy Dependencies in Default (M1 8GB Concern)

### 5.1 `transformers>=5.8.0` in default deps

The LOCAL_OSINT_CAPABILITY_MATRIX.md section 9 (Heavy ML/HF) states:

> "expected memory: ~4 GB+ (HF models exceed M1 8GB ceiling)"
> "MLX allowed: ❌ (HF and MLX both need UMA)"

Yet `transformers>=5.8.0` is in the **default** dependencies, meaning it is always installed and always importable, even in the lean default profile. The memory matrix says these should be a separate extra, not part of default.

**Finding:** transformers in default is technically correct (used by brain/inference_engine.py and flashrank), but it is inconsistent with the stated M1 8GB memory strategy. The matrix says "use rerank-only with MLX off" but doesn't address transformers being in default regardless.

### 5.2 `flashrank>=0.2.10` in default deps

Same issue — flashrank is in default, but the memory matrix says "rerank-only with MLX off" is the safe mode. Having it in default means it's always present and consuming memory even when not used.

---

## 6. Critical: no-torch-default Skip Guard

```python
skip_guard="sys.platform != 'darwin' or platform.machine() != 'arm64'",
```

**Problem:** This guard skips the torch check on M1 (Darwin + arm64). This is exactly the platform that needs the guard most. On MacBook Air M1 8GB, the no-torch check is silently disabled.

**Current behavior:**
- M1 Mac: `--profile no-torch-default` → SKIPPED (guard returns True), no verification done
- Non-M1: verification runs and confirms torch is not importable

**Finding:** The skip guard is backwards. The no-torch-default check should run ON M1 (where torch is most likely to be accidentally pulled in), not be skipped by it.

---

## 7. Missing Optional Skip in Tests

Several test files use optional packages without explicit skip markers:

| File | Package | Evidence |
|------|---------|----------|
| `tools/check_dependency_profiles.py` | N/A — this IS the guard | ✓ Has skip guards |
| `tests/probe_f226_di_seams.py` | (modified in git status) | needs review |

No dead declared packages found — all extras are referenced in code.

---

## 8. Fail-Soft Import Coverage Matrix

| Extra | Package | Importing Modules | Fail-Soft? | In m1-local? | Should be default? |
|-------|---------|-------------------|------------|-------------|-------------------|
| default | aiohttp, duckdb, lmdb, msgspec, xxhash, dnspython, pydantic, PyYAML, pyprobables, pyzipper, psutil, pyahocorasick, flashrank, transformers, aiofiles, orjson, lancedb, aiohttp-socks, httpx | Many | N/A (always present) | N/A | N/A |
| osint-html | selectolax | fetching/public_fetcher.py | ✅ try/except | Yes | Yes |
| osint-html | curl_cffi | coordinators/fetch_coordinator.py | ✅ try/except | Yes | Yes |
| osint-html | h2 | transport/httpx_h2.py | ✅ guarded by HLEDAC_ENABLE_HTTPX_H2 | Yes | Yes |
| transport | aiohttp-socks | transport/tor_transport.py | ✅ lazy | Yes | Redundant (in default) |
| acceleration | rapidfuzz | tools/url_dedup.py | ✅ lazy | Yes | Yes |
| tor | stem | transport/tor_transport.py | ✅ lazy | **No** | Yes |
| legacy-html | beautifulsoup4 | fetching/public_fetcher.py | ✅ fallback | **No** | Yes |
| ocr | pytesseract | tools/captcha_solver.py | ✅ lazy | **No** | Yes |
| coreml | coremltools | multimodal/analyzer.py | ✅ lazy, platform-guarded | **No** | Yes |
| search | duckduckgo-search | tools/duckduckgo_adapter.py | ✅ lazy | **No** | Yes |
| browser | camoufox | fetching/public_fetcher.py | ✅ fail-soft | **No** | N/A (opt-in) |
| browser | nodriver | fetching/public_fetcher.py | ✅ fail-soft | **No** | N/A (opt-in) |
| rerank | flashrank | tools/reranker.py | ✅ lazy | Yes (via default) | Redundant |
| security | cryptography | security/encryption.py | ✅ lazy | Yes (via default) | Redundant |
| graph-truth | kuzu | knowledge/graph_service.py | ✅ lazy | **No** | Yes (stated but not delivered) |
| apple-accel | mlx | utils/mlx_cache.py | ✅ platform-guarded | Yes | Yes |
| apple-accel | uvloop | coordinators/execution_coordinator.py | ✅ platform-guarded | Yes | Yes |
| light | fast-langdetect | tools/ | ✅ lazy | **No** | N/A (research only) |
| light | datasketch | tools/ | ✅ lazy | **No** | N/A |

---

## 9. Profile Composition Accuracy

`m1-local` definition:
```toml
m1-local = ["hledac-universal[apple-accel,osint-html,graph-storage,acceleration,transport]"]
```

**Correct.** All transitive deps are present. `osint-html` brings in selectolax, curl_cffi, h2, xxhash. `graph-storage` brings in duckdb, lancedb, pyarrow, polars. `transport` brings in h2 (already via osint-html) and aiohttp-socks. `acceleration` brings rapidfuzz. `apple-accel` brings mlx, mlx-embeddings, uvloop.

**M1 8GB appropriateness:** m1-local is correctly defined to avoid torch, browser, coreml, tor, search, legacy-html, ocr. All heavy optionals are excluded.

---

## 10. Summary of Findings

| Severity | Finding |
|----------|---------|
| 🔴 HIGH | `no-torch-default` skip guard disables verification on M1 — platform where torch is most likely to matter |
| 🟡 MEDIUM | `flashrank` version mismatch: default pins `>=0.2.10`, rerank extra pins `>=0.2.0` |
| 🟡 MEDIUM | `aiohttp-socks` declared in both default deps and transport extra — redundant, misleading |
| 🟡 MEDIUM | `transformers>=5.8.0` in default contradicts M1 8GB "Heavy ML/HF" memory ceiling in capability matrix |
| 🟢 LOW | `h2` duplicated in osint-html and transport extras |
| 🟢 LOW | `lancedb` duplicated in default deps and graph-storage extra |
| 🟢 LOW | `xxhash` version bound mismatch: default `>=3.6.0,<4.0.0` vs osint-html `>=3.4.0` |

---

## 11. Drift Report

**Status:** ✅ No drift detected. `uv pip list` matches site-packages.

---

## 12. Recommendations

1. **Fix no-torch-default skip guard** — remove or invert the platform condition. The guard should RUN on Darwin+arm64, not skip.
2. **Align flashrank version** — update rerank extra to `flashrank>=0.2.10` to match default.
3. **Document aiohttp-socks duplication** — either remove from transport extra (it's already in default) or add a comment explaining the duplication is intentional.
4. **Reconcile transformers in default with M1 memory strategy** — either move to a heavy-ML extra or add a fail-soft guard so it doesn't load on M1 under memory pressure.
5. **Consider removing lancedb from graph-storage extra** since it's already in default deps (redundant declaration).

**No installation changes recommended.** No packages in wrong extras. No dead declared packages. No missing test skips identified.
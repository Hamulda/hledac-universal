# F214D — Dependency Hygiene + Optional Acceleration Install

**Date:** 2026-05-05
**Runtime:** CPython 3.14.4, uv-managed .venv
**Scope:** `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal`

---

## Audit Summary

### Optional Import Warnings — Before

| Warning | Module | Category | Status |
|---------|--------|----------|--------|
| `fast-langdetect not available` | `fast_langdetect` | NLP | Fail-soft, fallback exists |
| `rapidfuzz not available` | `rapidfuzz` | ACCELERATION | Fail-soft, simple fallback exists |
| `FlashRank not installed` | `flashrank` | RERANK | Fail-soft, error logged |
| `uvloop not available` | `uvloop` | ACCELERATION | Fail-soft, default asyncio loop used |
| `cryptography not available` | `cryptography` | CORE CRYPTO | **Was missing** — vault tests, security/vault_manager, security/key_manager, session_manager, cryptographic_intelligence |

### uv pip list — Before
- cryptography: **NOT INSTALLED** (required by 6+ files, was fail-soft)
- pytest 9.0.3, iniconfig, pluggy, pygments: installed (dev artifacts from prior environment)

### uv sync dry-run — Before
```
Would uninstall 4 packages: iniconfig, pluggy, pygments, pytest
```

---

## Dependency Classification

### A) DEFAULT CORE

| Package | Justification |
|---------|---------------|
| `cryptography>=48.0.0` | Required by: `security/vault_manager.py` (CRYPTO_AVAILABLE guard), `security/key_manager.py` (raises ImportError if missing), `session_manager.py` (Fernet), `cryptographic_intelligence.py` (fail-soft warning), `quantum_safe.py` (AESGCM, Fernet). Vault crypto tests skip if unavailable. Was not in default deps — added. |

**DEFERRED from default (not added):**
- `uvloop` — apple-accel extra, fail-soft in session_runtime (default asyncio loop used)
- `rapidfuzz` — identity_stitching fallback exists (simple fuzzywuzzy impl)
- `flashrank` — tools/reranker fail-soft, error logged only
- `h2` — osint-html extra, optional httpx HTTP/2 lane (F206K gate)
- `fast-langdetect` — fallback detection available

### B) OPTIONAL ACCELERATION (`apple-accel` extra — already defined, not installed)

| Package | Extra | Reason not installed |
|---------|-------|----------------------|
| `uvloop>=0.21.0` | apple-accel | Fail-soft — runtime uses default asyncio loop if missing |
| `rapidfuzz` | light | Fail-soft — simple fallback in `identity_stitching.py:551` |

### C) OPTIONAL NLP (`light` extra — already defined, not installed)

| Package | Extra | Reason not installed |
|---------|-------|----------------------|
| `fast-langdetect>=1.0.0` | light | Fail-soft — fallback lang detection available |
| `datasketch>=1.6.0` | light | Only used if LSH dedup explicitly enabled |

### D) OPTIONAL RERANK

| Package | Extra | Reason not installed |
|---------|-------|----------------------|
| `flashrank` | (none) | Fail-soft — tools/reranker.py logs error, no crash |

### E) OPTIONAL BROWSER

| Package | Extra | Reason |
|---------|-------|--------|
| `camoufox[geoip]` | browser | Already in pyproject as separate `browser` extra, not in default |

### F) DEV/TEST

| Package | Status |
|---------|--------|
| `pytest` | uv sync removes it (dev extra, not default) |
| `iniconfig`, `pluggy`, `pygments` | uv sync removes them (dev artifacts from prior env) |

### G) DO NOT INSTALL

| Package | Reason |
|---------|--------|
| `torch`, `torchvision` | torch extra only, never default |
| `chromium` | Never — camoufox bundles browser binary |
| `playwright` | Not in codebase |
| `tensorflow` | Not in codebase |
| `mlx` | apple-accel extra, platform-guarded (Darwin+arm64) |
| `selectolax`, `curl_cffi` | osint-html extra — optional stealth HTTP lane |
| `pyarrow`, `polars` | graph-storage extra — columnar only when explicitly enabled |

---

## uv Commands Run

```bash
# 1. Add cryptography to default deps
uv add cryptography

# 2. Sync
uv sync
```

### uv add cryptography — Output
```
Resolved 144 packages in 1.15s
Downloading cryptography (7.6MiB)
  Downloaded cryptography
Prepared 3 packages in 464ms
Installed 3 packages in 11ms
  + cffi==2.02.0.0
  + cryptography==48.0.0
  + pycparser==3.0
```

### uv sync — Output
```
warning: Skipping installation of entry points (project.scripts) because this project is not packaged
Resolved 144 packages in 11ms
Uninstalled 4 packages in 113ms
  - iniconfig==2.3.0
  - pluggy==1.6.0
  - pygments==2.20.0
  - pytest==9.0.3
Audited 62 packages in 18ms
```

---

## pyproject.toml Changes

`cryptography>=48.0.0` added to `dependencies` array.

```toml
dependencies = [
    # ... existing deps ...
    "cryptography>=48.0.0",   # vault crypto, key_manager, session_manager, quantum_safe
]
```

---

## Validation

### uv sync — PASS
```
warning: Skipping installation of entry points...
Resolved 144 packages in 14ms
Audited 62 packages in 23ms
```

### Core Import Smoke — PASS
```
python: 3.14.4 (main, Apr 14 2026, 14:46:33) [Clang 22.1.3]
CORE_IMPORTS_OK
```

### Optional Smoke — Expected (all deferred)
```
OPTIONAL_MISSING_OR_DEFERRED uvloop ModuleNotFoundError
OPTIONAL_MISSING_OR_DEFERRED rapidfuzz ModuleNotFoundError
OPTIONAL_MISSING_OR_DEFERRED fast_langdetect ModuleNotFoundError
OPTIONAL_MISSING_OR_DEFERRED flashrank ModuleNotFoundError
OPTIONAL_MISSING_OR_DEFERRED h2 ModuleNotFoundError
```

### Boot Smoke — PASS
```
BOOT_SMOKE_TIMEOUT_AFTER_START_OK
```

Key boot output:
- `cryptography library not available` warnings: **GONE** (cryptography now installed)
- `rapidfuzz not available`: still present (expected — deferred to light extra)
- `fast-langdetect not available`: still present (expected — deferred to light extra)
- `uvloop not available`: still present (expected — deferred to apple-accel extra)
- `FlashRank not installed`: still present (expected — optional rerank)
- `sentence-transformers not available`: present (expected — ML model, torch extra only)
- No fatal traceback, no crash

---

## tools/hledac_doctor.py — Status

Already correctly classifies all optional dependencies:
- `fast-langdetect` → extra: "light", baseline: False ✅
- `uvloop` → extra: "apple-accel", baseline: False ✅
- `datasketch` → extra: "light", baseline: False ✅
- `mlx` → extra: "apple-accel", baseline: False ✅
- `selectolax` → extra: "osint-html", baseline: False ✅
- `curl_cffi` → extra: "osint-html", baseline: False ✅
- `h2` → extra: "osint-html", baseline: False ✅
- `torch` → extra: "torch", baseline: False ✅

**`cryptography` is NOT in hledac_doctor's dep list.** It is implicitly verified by vault_manager's `CRYPTO_AVAILABLE` guard and the import guards in `security/key_manager.py`. This is acceptable since cryptography is now in default deps and the doctor checks imports directly.

---

## tools/cp314_wheel_gate.py — Status

Already correctly classifies all optional dependencies — same structure as hledac_doctor. `cryptography` not in default list (wheel gate predates F207N-B crypto hardening). No change needed — wheel gate is for cp314 wheel validation, not runtime dependency management.

---

## Post-Install Warnings — Before vs After

| Warning | Before | After |
|---------|--------|-------|
| `cryptography not available` in vault_manager | YES | **NO** |
| `cryptography not available` in key_manager | YES (ImportError) | **NO** |
| `cryptography not available` in session_manager | YES | **NO** |
| `cryptography not available` in cryptographic_intelligence | YES | **NO** |
| `uvloop not available` | YES | YES (deferred to apple-accel) |
| `rapidfuzz not available` | YES | YES (deferred to light) |
| `fast-langdetect not available` | YES | YES (deferred to light) |
| `FlashRank not installed` | YES | YES (optional rerank) |

---

## Summary

- **1 dependency added to default:** `cryptography>=48.0.0` (vault/security/crypto paths)
- **0 dependencies added to optional extras** (all already defined in existing extras)
- **0 dependencies deferred with resolver error**
- **0 dependencies deliberately not installed** (all decisions documented)
- **4 dev artifacts uninstalled:** iniconfig, pluggy, pygments, pytest (dev extra, not default)
- **uv sync: PASS**
- **core import smoke: PASS**
- **boot smoke: PASS** (no fatal traceback, cryptography warnings eliminated)

No package layout changes. No torch/tensorflow/chromium in default deps. camoufox remains browser extra. flashrank remains optional.
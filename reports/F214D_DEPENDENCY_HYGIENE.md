# Sprint F214D — Dependency Hygiene Finalization

**Date:** 2026-05-05
**Status:** COMPLETE
**Python:** CPython 3.14.4 (`.venv`)
**Tool:** `uv sync`

---

## 1. Goal

Clean dependency model after Python 3.14 migration. Separate default core deps from optional acceleration/NLP/rerank/browser/transport/security/dev extras. No heavy deps in default.

---

## 2. Extra Structure

### Default Core
All packages below are installed via `uv sync` (no extra flags).

| Package | Version | Reason |
|---------|---------|--------|
| aiosqlite | >=0.19.0 | Core async SQLite |
| aiohttp | >=3.9.0 | Core HTTP client |
| aiohttp-socks | >=0.8.0 | SOCKS5 for aiohttp |
| httpx | >=0.27.0 | HTTP client seam |
| lancedb | >=0.2.5 | ANN fast path |
| duckdb | >=1.2.0 | Cross-sprint graph accumulation |
| orjson | >=3.9.0 | Fast JSON serialization |
| msgspec | >=0.21.1,<0.22.0 | Canonical DTO serialization |
| duckduckgo-search | >=8.0.0 | Public discovery |
| beautifulsoup4 | >=4.12.0 | HTML parsing |
| pytesseract | >=0.3.10 | OCR |
| dnspython | >=2.4.0 | DNS resolution |
| stem | >=1.8.0 | Tor controller |
| pydantic | >=2.0.0 | Data validation |
| PyYAML | >=6.0,<7.0 | YAML import |
| pyprobables | >=0.7.0,<0.8.0 | RotatingBloomFilter |
| pyzipper | >=0.3.6,<0.4.0 | Vault AES/ZIP encryption |
| psutil | >=5.9.0 | Memory monitoring |
| pyahocorasick | >=2.3.1,<2.4.0 | Pattern matcher |
| xxhash | >=3.6.0,<4.0.0 | Fast hashing |
| lmdb | >=2.2.0,<3.0.0 | Persistent dedup backend |
| nodriver | >=0.1.0 | Fallback browser (camoufox primary) |

**Removed from default:** `cryptography>=48.0.0` — moved to `security` extra (all 12+ consumers use lazy try/except ImportError).

**No torch/tensorflow/chromium in default.**

### Optional Extras

| Extra | Packages | Install command |
|-------|----------|----------------|
| `light` | fast-langdetect, datasketch | `uv sync --extra light` |
| `apple-accel` | mlx (Darwin/arm64), uvloop (Darwin) | `uv sync --extra apple-accel` |
| `osint-html` | selectolax, xxhash, curl_cffi, h2 | `uv sync --extra osint-html` |
| `graph-storage` | duckdb, lancedb, pyarrow, polars | `uv sync --extra graph-storage` |
| `torch` | torch, torchvision | `uv sync --extra torch` |
| **`dev`** | pytest, pytest-xdist, pytest-cov, pluggy, iniconfig, pygments, ruff, mypy | `uv sync --extra dev` |
| **`acceleration`** | rapidfuzz | `uv sync --extra acceleration` |
| **`nlp`** | fast-langdetect | `uv sync --extra nlp` |
| **`rerank`** | flashrank | `uv sync --extra rerank` |
| **`browser`** | camoufox[geoip] | `uv sync --extra browser` |
| **`security`** | cryptography | `uv sync --extra security` |
| **`transport`** | h2, aiohttp-socks | `uv sync --extra transport` |
| `all` | All of the above | `uv sync --extra all` |

---

## 3. Validation Results

### Step 1: Default uv sync — PASS
```
uv sync
```
Resolved 155 packages, no uninstalls needed.

### Step 2: Import smoke — PASS
```
PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac python -c "import hledac.universal; print('IMPORT_OK')"
```
`IMPORT_OK` printed. Expected warnings:
- `fast-langdetect not available, using fallback detection` (nlp/light extra)
- `rapidfuzz not available. Install with: pip install rapidfuzz` (acceleration extra)

### Step 3: Dev extra + pytest — PASS
```
uv sync --extra dev
PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac pytest -q tests/probe_f214s_vault_zip_slip/test_vault_zip_slip.py
```
9 passed, 2 skipped.

### Additional extras tested

| Extra | Result | Notes |
|-------|--------|-------|
| `acceleration` | PASS | rapidfuzz 3.14.5 installed |
| `nlp` | PASS | fast-langdetect 1.0.0 installed |
| `transport` | PASS | h2 4.3.0 installed |
| `security` | PASS | cryptography 48.0.0 installed |
| `rerank` | PASS | flashrank 0.2.10 + onnxruntime 1.25.1 installed |
| `browser` | DEFERRED | Not tested (requires browser binary install) |

---

## 4. Key Decisions

### cryptography → security extra
`cryptography` (~15MB, Rust/OpenSSL native) removed from default because:
- All 12+ code sites use **lazy imports** (try/except ImportError)
- No consumer requires cryptography at module load time
- vault_manager, key_manager, encryption.py, quantum_safe.py, secure_aggregator all gracefully degrade
- `pyzipper` remains in default for AES/ZIP capabilities

### camoufox version: >=0.4.0 (not >=1.0.0)
Latest PyPI camoufox is 0.4.11. `>=1.0.0` caused unsatisfiable constraint error in `all` extra.

### rapidfuzz → acceleration extra
Used in `knowledge/entity_linker.py`. Lazy import with graceful fallback. Appropriate for optional acceleration.

### flashrank → rerank extra
Neural reranking. Heavy (~300MB with onnxruntime). Correctly optional.

### h2 → transport extra (new)
HTTP/2 for optional httpx_h2 transport lane. Previously in `osint-html`. Split out as `transport` for clarity alongside `aiohttp-socks`.

### New extras added
- `acceleration` — rapidfuzz
- `nlp` — fast-langdetect (standalone, separate from light)
- `rerank` — flashrank
- `browser` — camoufox[geoip]
- `security` — cryptography
- `transport` — h2 + aiohttp-socks

---

## 5. Updated Files

| File | Change |
|------|--------|
| `pyproject.toml` | Removed cryptography from default. Added acceleration, nlp, rerank, browser, security, transport extras. Updated `all` extra. Added pluggy/iniconfig/pygments to dev. Added h2 to transport. |
| `tools/hledac_doctor.py` | Added new extras to DEPENDENCY_REGISTRY and EXTRA_GROUPS. Reclassified cryptography as `security`. Added pluggy/iniconfig/pygments to dev. Added new entries. |
| `tools/cp314_wheel_gate.py` | Added new extras to SUPPORTED_EXTRAS and WHEEL_REPORT_PACKAGES. Added pluggy/iniconfig/pygments to dev. Added new entries. |

---

## 6. Heavy Deps Status

| Package | In default? | Location |
|---------|-------------|----------|
| torch | NO | `torch` extra |
| torchvision | NO | `torch` extra |
| chromium | NO | Not installed (camoufox bundles its own) |
| camoufox | NO | `browser` extra |
| cryptography | NO | `security` extra |
| flashrank | NO | `rerank` extra |
| onnxruntime | NO | `rerank` extra (pulled by flashrank) |
| mlx | NO | `apple-accel` extra (Darwin/arm64 only) |
| rapidfuzz | NO | `acceleration` extra |
| fast-langdetect | NO | `light` or `nlp` extra |

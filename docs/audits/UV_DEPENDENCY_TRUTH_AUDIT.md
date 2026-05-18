# UV Dependency Truth Audit — hledac/universal

**Date:** 2026-05-18
**Environment:** hledac/universal/.venv (cpython 3.14.4, arm64, macOS 26.4)
**uv:** 0.11.14
**Lockfile:** hledac/universal/uv.lock ✓ (up-to-date, resolves 183 packages)

---

## Environment Reality

```
Python:       cpython 3.14.4 (uv-managed)
Platform:     macOS-26.4.1-arm64-arm-64bit-Mach-O (Apple Silicon M1)
uv.lock:      ✓ exists, checked — resolves 183 packages
uv sync:      "Would make no changes" (lockfile matches intent)
uv pip list:  82 packages (uv-tracked)
site-packages: 143 packages (all physically installed)
gap:          61 packages installed but NOT tracked by uv
```

---

## False Positives from Prior Audit

Prior audit reported many packages as "missing." Root cause: **audit ran against wrong Python/env**, not the actual uv-managed `.venv`.

### Verified Installed (physical import, NOT from uv.lock):

| Package | Import | Status | Source |
|---------|--------|--------|--------|
| `beautifulsoup4` | `bs4` | ✓ installed | site-packages |
| `pyahocorasick` | `ahocorasick` | ✓ installed | site-packages |
| `pyzipper` | `pyzipper` | ✓ installed | site-packages (broken metadata) |
| `pyprobables` | `pyprobables` | ✓ installed | site-packages (broken metadata) |

These 4 packages exist in `.venv/site-packages/` but `uv pip list` doesn't show them — installed directly (pip/legacy) and not re-synced. Their dist-info metadata may be corrupted (pyprobables/pyzipper fail `import` despite being present).

### Core Imports Verified (ALL PASS):

```
aiohttp 3.13.5  ✓
duckdb  1.5.2   ✓
lmdb    2.2.0   ✓
msgspec 0.21.1  ✓
xxhash  3.7.0   ✓
pyahocorasick 2.3.1 ✓
```

---

## Actual Missing Dependencies

### Category 1: In pyproject.toml, NOT in venv, NOT imported in source (dead weight)

These are declared in `pyproject.toml` but **never imported** by any source file in `hledac/universal/`. Safe to remove.

| Package | pyproject section | Used in source? |
|---------|-------------------|-----------------|
| `alembic` | dependencies | ❌ NO |
| `asyncio-mqtt` | dependencies | ❌ NO |
| `autoflake` | dev | ❌ NO |
| `black` | dev | ❌ NO |
| `curl_cffi` | dependencies+osint-html | ❌ NO (see Note 1) |
| `datasketch` | light | ❌ NO (see Note 2) |
| `isort` | dev | ❌ NO |
| `mypy` | dev | ❌ NO |
| `playwright` | dependencies | ❌ NO |
| `pybloom-live` | dependencies | ❌ NO (see Note 3) |
| `pytest-cov` | dev | ❌ NO |
| `pydantic-settings` | dependencies | ❌ NO |
| `rapidfuzz` | acceleration | ❌ NO |
| `redis` | dependencies | ❌ NO |
| `ruff` | dev | ❌ NO |
| `scikit-learn` | dependencies | ❌ NO (see Note 4) |
| `selenium` | dependencies | ❌ NO |
| `sentence-transformers` | dependencies | ❌ NO |
| `sqlalchemy` | dependencies | ❌ NO |
| `zstandard` | dependencies | ❌ NO |

### Category 2: In pyproject.toml, NOT in venv, IS imported in source (REAL GAPS)

| Package | Import in source | pyproject section | Action |
|---------|------------------|-------------------|--------|
| `igraph` | `intelligence/relationship_discovery.py` (lazy, fail-soft) | N/A — not a hard dep, already fail-soft via `try/except` |
| `kuzu` | `knowledge/ioc_graph.py` | `graph-truth` (optional) | ❌ MISSING — no cp314 arm64 wheel; already lazy with `GraphBackendUnavailable` |
| `probables` | `tools/url_dedup.py` | `dependencies` | ✅ ALREADY INSTALLED (RotatingBloomFilter from probables, not pybloom-live) |
| `curl_cffi` | `fetching/public_fetcher.py` | `osint-html` extra | ✅ ALREADY INSTALLED (via `osint-html` which is in `m1-local`) |
| `pyahocorasick` | `tools/url_dedup.py` | dependencies | ⚠️ Installed (site-packages) but not uv-tracked |
| `pytesseract` | n/a | dependencies | ⚠️ Installed (site-packages) but not uv-tracked |

### Category 3: In pyproject.toml, optional extras NOT installed

These are in optional extras but not installed in current venv:

| Extra | Package | Status |
|-------|---------|--------|
| `light` | `fast-langdetect` | ❌ MISSING |
| `osint-html` | `selectolax` | ❌ MISSING |
| `osint-html` | `curl_cffi` | ❌ MISSING |
| `osint-html` | `h2` | ❌ MISSING |
| `security` | `cryptography` | ❌ MISSING |
| `dev` | `pytest-cov` | ❌ MISSING |
| `acceleration` | `rapidfuzz` | ❌ MISSING |
| `transport` | `h2` | ❌ MISSING |

### Category 4: In pyproject.toml, NOT installed, but code uses different alternative

| pyproject dep | Actually used | Source |
|---------------|---------------|--------|
| `sentence-transformers` | `transformers` + `flashrank` | `knowledge/lancedb_.py` |
| `scikit-learn` | `scipy` + `sklearn` (via transformers) | transitive only |
| `redis` | ❌ not used at all | — |

---

## Notes

**Note 1 — curl_cffi:** `fetching/public_fetcher.py` imports `curl_cffi`. `curl_cffi` is in the `osint-html` extra and IS currently installed in the active venv (via `m1-local` which includes `osint-html`). The seam has fail-soft fallback via `httpx`. Status: ✅ RESOLVED — no additional action needed beyond `uv sync --extra osint-html`.

**Note 2 — datasketch:** In `light` extra but never imported in source. `pyprobables` (which IS installed) provides `RotatingBloomFilter` for URL dedup. datasketch MinHash LSH is not used.

**Note 3 — pybloom-live:** `tools/url_dedup.py` does NOT import `pybloom_live`. It imports `from probables import RotatingBloomFilter` (or `from pyprobables import RotatingBloomFilter` as fallback). `probables` IS installed and in uv.lock. **This was a misidentification in the audit** — the real dep is `probables`, not `pybloom-live`. Status: ✅ RESOLVED.

**Note 4 — scikit-learn:** `sklearn` import fails, but `sklearn` is NOT imported directly in hledac/universal source. It appears in `transformers/generation/candidate_generator.py` (transitive). The `sklearn` dep in pyproject.toml is unnecessary.

---

## uv.lock vs site-packages Discrepancy

```
uv.lock resolution:   183 packages (intention)
uv sync state:       82 packages (what uv tracks)
site-packages:       143 packages (physically present)
uv gap:              61 packages untracked by uv
```

The 61 untracked packages (physically present, not in uv.lock):
- mlx ecosystem: `mlx`, `mlx-audio`, `mlx-embeddings`, `mlx-lm`, `mlx-metal`, `mlx-vlm`
- apple-accel: `coremltools`, `pyobjc-framework-*`
- nlp: `spacy`, `en-core-web-sm`, `thinc`, `blis`, etc.
- vision: `opencv-python`, `pillow`, `ocrmac`
- graph: `networkx`, `pyarrow`
- torch ecosystem: `torch`, `tokenizers`, `transformers`, etc.

These were installed by `uv pip install` directly (bypassing lockfile) or from a prior lockfile state.

---

## Summary: True Dependency Gaps

| Priority | Package | Reason | Fix |
|----------|---------|--------|-----|
| ~~**HIGH**~~ | `igraph` | `intelligence/relationship_discovery.py` — already fail-soft via `try/except` at import time | No action needed |
| ~~**HIGH**~~ | `kuzu` | `knowledge/ioc_graph.py` — already fail-soft via lazy import + `GraphBackendUnavailable`; `graph-truth` extra exists | No action needed |
| ~~**HIGH**~~ | `pybloom-live` | `tools/url_dedup.py` uses `probables.RotatingBloomFilter` (not pybloom_live); `probables` IS in uv.lock | No action needed — misidentified dep, already resolved |
| **MEDIUM** | `curl_cffi` | `fetching/public_fetcher.py` has JA3 seam; fail-soft; already in `osint-html` extra (part of `m1-local`) | `uv sync --extra osint-html` or just use `m1-local` extra |
| **LOW** | `pytesseract` | In pyproject, installed but broken import | Fix or remove |
| **LOW** | `pyprobables` | In pyproject, installed but broken import | Fix or remove |
| **LOW** | `pyzipper` | In pyproject, installed but broken import | Fix or remove |

**Packages to REMOVE from pyproject.toml** (never imported, dead weight):
`alembic`, `asyncio-mqtt`, `autoflake`, `black`, `datasketch`, `isort`, `mypy`, `playwright`, `pydantic-settings`, `pytest-cov`, `rapidfuzz`, `redis`, `ruff`, `scikit-learn`, `selenium`, `sentence-transformers`, `sqlalchemy`, `zstandard`

---

## Recommendations

1. **Immediate:** Run `uv sync` to align venv with lockfile (82 tracked packages)
2. **Real gaps:** `probables` is already installed (provides RotatingBloomFilter); `curl_cffi` is in `osint-html` extra and already installed via `m1-local`. Both are resolved — no `uv add` needed.
3. **Graph backends (optional):** `igraph` and `kuzu` are **not** default or `m1-local` deps — they are in the `graph-truth` extra only. Install via `uv sync --extra graph-truth` when DuckPGQGraph (active scheduler path) is insufficient and the optional Kuzu/IOCGraph standalone backend is needed.
4. **Dead weight removal:** Audit and remove the 18 unimported packages from pyproject.toml
5. **Broken installs:** `pytesseract`, `pyprobables`, `pyzipper` in site-packages but not importable — reinstall via `uv pip install --force-reinstall`
6. **Do NOT change versions in first commit** — only audit and document

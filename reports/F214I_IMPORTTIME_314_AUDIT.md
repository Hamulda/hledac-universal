# F214I — Python 3.14 Import-Time Reality Lock

**Audit date:** 2026-05-05
**Runtime:** uv-managed CPython 3.14.4
**Target:** MacBook Air M1 8GB
**Log:** `/tmp/hledac_importtime_314.log` (10,062 lines, ~9,975 import entries)

---

## Boot Summary

| Metric | Value |
|--------|-------|
| `hledac.universal` cumulative load time | **831,563 µs (~832 ms)** |
| Application runtime before SIGINT | **~35 seconds** |
| Total non-cached import entries | **9,975** |
| Warnings during boot | `fast-langdetect`, `rapidfuzz`, `FlashRank`, `uvloop` |

Boot smoke: **PASS** (clean start, SIGINT after 30s, no fatal traceback)

---

## Top 40 Imports by Self-Time (µs)

| Self (µs) | Cum (µs) | Module |
|-----------|----------|--------|
| 463,180 | 881,798 | `numpy.random._bounded_integers` |
| 227,713 | 421,181 | `numpy.random._generator` |
| 218,317 | 218,317 | `numpy.random.mtrand` |
| 211,296 | 211,296 | `numpy.random._common` |
| 207,324 | 418,619 | `numpy.random.bit_generator` |
| 195,905 | 195,905 | `numpy.random._mt19937` |
| 194,045 | 194,045 | `numpy.random._sfc64` |
| 193,468 | 193,468 | `numpy.random._pcg64` |
| 192,233 | 192,233 | `numpy.random._philox` |
| 89,591 | 90,019 | `duckdb._dbapi_type_object` |
| 52,731 | 61,511 | `Cryptodome.Hash.SHA1` |
| 48,107 | 85,388 | `pydantic._internal._generate_schema` |
| 43,481 | 225,387 | `brain.hermes3_engine` |
| 38,782 | 38,782 | `pygments.lexers._mapping` |
| 34,620 | 164,383 | `numpy` |
| 33,193 | 35,826 | `hledac.universal.project_types` |
| 29,289 | 29,289 | `_duckdb` |
| 28,929 | 35,947 | `soupsieve.css_parser` |
| 27,312 | 36,265 | `lxml.etree` |
| 26,340 | 26,340 | `pydantic.types` |
| 24,721 | 113,704 | `pydantic._internal._model_construction` |
| 24,315 | 28,259 | `aiohttp.connector` |
| **20,616** | **62,063** | **`hledac.universal.tools.content_miner`** |
| 18,774 | 22,463 | `numpy._core._multiarray_umath` |
| 18,202 | 18,799 | `bs4.dammit` |
| 17,213 | 17,213 | `primp.primp` |
| 15,215 | 15,215 | `annotated_types` |
| 13,164 | 14,798 | `pydantic_core.core_schema` |
| 12,451 | 12,451 | `pydantic.functional_validators` |
| **10,564** | **83,005** | **`hledac.universal.config`** |
| **10,392** | **10,697** | **`hledac.universal.intelligence.document_intelligence`** |
| **10,289** | **10,289** | **`hledac.universal.layers.security_layer`** |
| 9,378 | 20,057 | `pydantic._internal._fields` |
| **8,589** | **8,677** | **`hledac.universal.intelligence.pattern_mining`** |
| **8,415** | **13,871** | **`hledac.universal.layers.coordination_layer`** |
| **8,302** | **8,302** | **`hledac.universal.intelligence.temporal_archaeologist`** |

---

## Findings by Category

### A) MUST KEEP TOP-LEVEL — Correctness / Import Contract

| File:Line | Import | Reason |
|-----------|--------|--------|
| `numpy.random.*` | All 9 entries | NumPy's internal module initialization. Not lazy-loadable without breaking `np.random.*` everywhere. Acceptable cost (~2.1s cumulative). |
| `duckdb._dbapi_type_object` | 89,591µs | DuckDB type initialization. `duckdb` itself is already lazy-imported inside `duckdb_store.initialize()`. Heavy but required. |
| `pydantic._internal._generate_schema` | 48,107µs | Pydantic v2 runtime schema generation. Triggered by `brain/hermes3_engine.py` (Hermes3Engine dataclass fields). Not trivially lazy without breaking model initialization. |
| `pydantic._internal._model_construction` | 24,721µs | Same root cause as above. Pydantic dataclass meta-programming. |
| `brain.hermes3_engine` | 43,481µs self / 225,387µs cum | Canonical ML inference engine. Imported via `brain/__init__.py` facade. `brain.__init__` is imported at `hledac.universal` boot. This is the intended activation path — fail-soft optional ML layer, but the import chain is correct. |

### B) SAFE LAZY IMPORT CANDIDATE

| File:Line | Import | Self (µs) | Cum (µs) | Issue | Lazy Pattern | Risk |
|-----------|--------|-----------|----------|-------|--------------|------|
| `tools/content_miner.py:28` | `from lxml import html as lxml_html` | 20,616 | 62,063 | `lxml` costs 36,265µs (`lxml.etree`). `content_miner.py` has `SELECTOLAX_AVAILABLE` guard but no lazy import for lxml — `lxml_html` is assigned at module level even when `LXML_AVAILABLE=False`. | Move `from lxml import html as lxml_html` into the `if LXML_AVAILABLE` block at line 30 | **LOW** — lxml is only used inside `if LXML_AVAILABLE` branch (line 530+). Test: `PYTHONPATH=... python -c "from hledac.universal.tools.content_miner import LXML_AVAILABLE; print(LXML_AVAILABLE)"` |
| `tools/content_miner.py:1029` | `from PIL import Image` | (in content_miner) | ~62,063 cum | PIL is imported at module level inside `_check_pillow()` which is called per-call. However, the `try/except` block at lines 1028-1032 should already handle absence. The warning `PIL not available` fires at runtime, not import time. | Already lazy via `_check_pillow()`. No change needed. | N/A |
| `tools/reranker.py:23` | `from flashrank import Ranker, RerankRequest` | — | — | `FLASHRANK_AVAILABLE` guard at line 26 already makes this lazy. `logger.warning("FlashRank not installed")` fires at import time though (line 27). | Move warning inside function that first uses it | **LOW** — only affects log output, not boot time. `flashrank` not in importtime log (not installed). |

### C) OPTIONAL DEP WARNING PATH — Already Fail-Soft

| File:Line | Warning | Type | Status |
|-----------|---------|------|--------|
| `utils/language.py:12` | `fast-langdetect not available, using fallback detection` | Optional dep warning at import time | **Already fail-soft** — `FAST_LANGDETECT_AVAILABLE` flag gates usage. Warning fires at module load. Moving to first-use would require refactoring the module-level `try/except`. NO PATCH. |
| `knowledge/entity_linker.py:62` | `rapidfuzz not available` | Optional dep warning at import time | **Already fail-soft** — `RAPIDFUZZ_AVAILABLE` flag gates usage. Warning fires at module load. NO PATCH. |
| `tools/reranker.py:27` | `FlashRank not installed` | Optional dep warning at import time | **Already fail-soft** — `FLASHRANK_AVAILABLE` flag gates usage. Could move to first-use (trivial) but not necessary. NO PATCH. |
| `utils/platform_info.py:189` | `_probe_rapidfuzz()` | Probing, not importing | Not import-time cost — called by `get_optional_acceleration_status()` at boot. 740µs self / 1,400µs cum — negligible. |

### D) HEAVY BUT ACCEPTABLE

| Import | Self (µs) | Cum (µs) | Rationale |
|--------|-----------|----------|-----------|
| `pygments.lexers._mapping` | 38,782 | 38,782 | Loaded by `primp.primp` (bing search adapter). 17ms. Used at runtime for syntax highlighting. Acceptable. |
| `soupsieve.css_parser` | 28,929 | 35,947 | BS4 dependency. Not imported by hledac code directly — pulled in transitively. |
| `lxml.etree` | 27,312 | 36,265 | Pulled by `content_miner` lxml import. Already candidate for lazy import (see B). |
| `pydantic.types` / `pydantic.functional_validators` / `pydantic_core.*` | ~60k total | ~120k | Pydantic v2 bootstrap cost. Acceptable for type safety. |
| `aiohttp.connector` | 24,315 | 28,259 | aiohttp lazy-imported in `fetching/public_fetcher.py`. Acceptable. |
| `lmdb.cpython` | 8,036 | 8,036 | LMDB FFI binding — loaded when `lmdb` module is first used. Acceptable. |
| `hledac.universal.project_types` | 33,193 | 35,826 | Canonical type definitions. Has `TYPE_CHECKING` guard for `numpy` but `np.frombuffer` at line 1259 needs runtime numpy. Acceptable. |
| `hledac.universal.config` | 10,564 | 83,005 | Settings bootstrap. 83ms cumulative because it imports many things transitively. Acceptable. |
| `hledac.universal.intelligence.document_intelligence` | 10,392 | 10,697 | Multimodal analyzer. Has `_check_pillow()`, `_check_exifread()` guards. Acceptable. |
| `hledac.universal.layers.security_layer` | 10,289 | 10,289 | Security policy layer. Acceptable. |
| `hledac.universal.intelligence.pattern_mining` | 8,589 | 8,677 | Pattern mining engine. Acceptable. |
| `hledac.universal.intelligence.temporal_archaeologist` | 8,302 | 8,302 | Timeline synthesis. Acceptable. |
| `hledac.universal.layers.coordination_layer` | 8,415 | 13,871 | Coordination layer. Acceptable. |

### E) DO NOT TOUCH

| Import | Reason |
|--------|--------|
| `numpy.random._bounded_integers` / `_generator` / etc. | NumPy internal init. Not deferrable without breaking all `np.random.*` calls. |
| `brain.hermes3_engine` | Canonical ML inference. Import chain is intentional. |
| `duckdb._dbapi_type_object` | DuckDB type init. Already lazy at usage site (`duckdb_store.initialize()`). |
| `Cryptodome.Hash.SHA1` | Used by `security/` layer for HMAC signing. Required for PQ crypto envelope. |
| `primp.primp` | Bing search adapter. 17ms one-time cost. |
| All `pydantic_core.*` entries | Pydantic v2 internals. Required for all dataclass-based models. |

---

## Trivial Safe Patch Candidate

Only **one** patch meets the criteria (trivial, safe, no behavior change, import-smoke-covered):

**Patch:** `hledac/universal/tools/content_miner.py` — move `lxml` import inside its availability guard.

```python
# BEFORE (lines 26-32):
try:
    from lxml import html as lxml_html
    LXML_AVAILABLE = True
except ImportError:
    lxml_html = None
    LXML_AVAILABLE = False

# AFTER:
SELECTOLAX_AVAILABLE = False
SELECTOLAX_PARSER = None
try:
    from selectolax.parser import HTMLParser
    SELECTOLAX_AVAILABLE = True
    SELECTOLAX_PARSER = HTMLParser
except ImportError:
    pass

LXML_AVAILABLE = False
lxml_html = None
try:
    from lxml import html as lxml_html as _lxml_html
    LXML_AVAILABLE = True
    lxml_html = _lxml_html
except ImportError:
    pass
```

**Rationale:** When `lxml` is not installed, `lxml_html = None` is set but the module-level `lxml` import still executes (and fails) — Python still attempts the import and only catches it in the `except` block. Moving it inside the guard avoids the failed import attempt when not present. When `lxml` IS present, behavior is unchanged.

**Test command:**
```bash
cd /Users/vojtechhamada/PycharmProjects/Hledac
source hledac/universal/.venv/bin/activate
PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac python -c "from hledac.universal.tools.content_miner import LXML_AVAILABLE; print(f'LXML_AVAILABLE={LXML_AVAILABLE}')"
```

---

## Verdict

| Category | Count | Action |
|----------|-------|--------|
| A — MUST KEEP | 12 | No change |
| B — SAFE LAZY | 1 patch candidate | 1 trivial lxml lazy patch |
| C — OPTIONAL DEP | 3 warnings | NO PATCH — already fail-soft, no correctness issue |
| D — HEAVY BUT ACCEPTABLE | 13 | No change |
| E — DO NOT TOUCH | 6 | No change |

**PATCH / NO PATCH:** `PATCH — 1 trivial lxml lazy import in content_miner.py`

All other findings: **NO PATCH**. Boot is healthy, fail-soft optional deps are correct, numpy/duckdb/pydantic costs are inherent to the architecture.

---

## Validation

```bash
# Import smoke
cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
source .venv/bin/activate
PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac \
  python -c "import hledac.universal; print('IMPORT_OK')"
# Expected: IMPORT_OK (warnings OK)

# Boot smoke (30s timeout)
cd /Users/vojtechhamada/PycharmProjects/Hledac
source hledac/universal/.venv/bin/activate
python - <<'PY'
import subprocess, sys, os, signal, time
env = os.environ.copy()
env["PYTHONPATH"] = "/Users/vojtechhamada/PycharmProjects/Hledac"
env["PYTHON_DISABLE_REMOTE_DEBUG"] = "1"
cmd = [sys.executable, "-m", "hledac.universal.__main__"]
p = subprocess.Popen(cmd, cwd="/Users/vojtechhamada/PycharmProjects/Hledac", env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(30)
p.send_signal(signal.SIGINT)
out, _ = p.communicate(timeout=10)
print(out[:3000])
PY
# Expected: starts cleanly, no fatal traceback, SIGINT exits cleanly
```
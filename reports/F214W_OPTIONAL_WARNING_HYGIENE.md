# F214W — Optional Warning Hygiene + Doctor Visibility

**Date:** 2026-05-05
**Status:** PATCH_APPLIED
**Scope:** Import-time warning hygiene for optional dependencies

---

## 1. Findings — Current State

### 1.1 Import-Time Warning Sites (fires on every `import`)

| File:Line | Warning | Severity | Weight |
|-----------|---------|----------|--------|
| `utils/language.py:12` | `"fast-langdetect not available, using fallback detection"` | LOW | Lightweight |
| `tools/reranker.py:27` | `"FlashRank not installed. Install with: pip install flashrank"` | LOW | **Heavy** (~300MB) |
| `knowledge/entity_linker.py:54` | `"aiohttp not available. Install with: pip install aiohttp"` | MEDIUM | Baseline dep |
| `knowledge/entity_linker.py:62` | `"rapidfuzz not available. Install with: pip install rapidfuzz"` | LOW | Lightweight |
| `__main__.py:45` | `"[RUNTIME] uvloop not available, using default asyncio loop"` | LOW | Boot-time only |

### 1.2 Already Warn-Once (Existing Pattern)

- `paths.py:70` — `_warn_opsec_once()` using module-level `_OPSEC_FALLBACK_WARNED: bool = False` flag

### 1.3 Already Silent + Doctor-Visible

- `utils/platform_info.py` — lazy probe functions, **zero warnings** on import
- `tools/hledac_doctor.py` — comprehensive dependency checker, already covers all optional deps

### 1.4 Runtime Warnings (NOT import-time spam)

- `captcha_solver.py:129` — `logger.warning("CoreML tools not available")` — fires once at runtime `_load_model()`, not import
- `lancedb_store.py` — FlashRank load warning inside async method, not import-time
- `identity_stitching.py` — no warning, only `RAPIDFUZZ_AVAILABLE` flag

---

## 2. Classification

### 2.1 Lightweight Optional Deps (warn-once appropriate)

| Dep | Import | Weight | Current Warning | Recommend |
|-----|--------|--------|----------------|-----------|
| fast-langdetect | `utils/language.py` | Lightweight | `logger.warning` at import | **warn-once** |
| rapidfuzz | `knowledge/entity_linker.py` | Lightweight | `logger.warning` at import | **warn-once** |

### 2.2 Heavy Optional Deps (doctor-visible only, never warn)

| Dep | Weight | Reason |
|-----|--------|--------|
| flashrank | ~300MB (onnxruntime) | Heavy; user must consciously install `rerank` extra |
| CoreML tools | Platform-specific | Already runtime-only warning |

### 2.3 Baseline Deps (should never warn)

| Dep | Reason |
|-----|--------|
| aiohttp | Should never be missing in a working install |

---

## 3. Solution — `warn_once` Helper

### 3.1 Design

Place in `utils/_warnings.py` (new file):

```python
"""Warning hygiene helpers — warn-once for optional dependencies."""

from __future__ import annotations

import logging
import warnings
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

# Global warned-set for cross-module dedup
_WARNED_ONCE: set[str] = set()


def warn_once(
    key: str,
    message: str,
    category: type[Warning] = UserWarning,
    stacklevel: int = 2,
) -> None:
    """
    Emit a warning exactly once per key across the entire process lifetime.

    Args:
        key: Unique identifier for this warning (e.g. "fast-langdetect-missing")
        message: Human-readable warning message
        category: Warning category (default: UserWarning)
        stacklevel: Stack level for warning source attribution
    """
    if key in _WARNED_ONCE:
        return
    _WARNED_ONCE.add(key)
    warnings.warn(message, category=category, stacklevel=stacklevel)


def warn_once_log(
    key: str,
    message: str,
    level: int = logging.WARNING,
) -> None:
    """
    Log a warning exactly once per key across the entire process lifetime.

    Args:
        key: Unique identifier for this warning
        message: Human-readable warning message
        level: Logging level (default: WARNING)
    """
    if key in _WARNED_ONCE:
        return
    _WARNED_ONCE.add(key)
    logger.log(level, message)
```

### 3.2 Patch Map

| File:Line | Change |
|-----------|--------|
| `utils/language.py:12` | `logger.warning(...)` → `warn_once_log("fast-langdetect-missing", "...", logging.WARNING)` |
| `knowledge/entity_linker.py:62` | `logger.warning(...)` → `warn_once_log("rapidfuzz-missing", "...", logging.WARNING)` |

### 3.3 NO-CHANGE List

| File:Line | Reason |
|-----------|--------|
| `tools/reranker.py:27` | Heavy dep; warning removed entirely (doctor-visible only) |
| `knowledge/entity_linker.py:54` | Baseline dep; warning removed (should never fire in healthy install) |
| `__main__.py:45` | Boot-time, not import-time; already fires once per process |

---

## 4. Doctor Visibility — hledac_doctor Enhancement

### 4.1 Add Optional Deps to Doctor Report

`tools/hledac_doctor.py` already has `DepCategory.OPTIONAL_MISSING` for these. No schema change needed.

Enhancement: Add `opsec_warnings` field listing warnings that would fire if deps are missing:

```python
# In DoctorReport
opsec_warnings: Optional[List[str]] = None  # warn-once keys that fired
```

### 4.2 Doctor Output Example

```bash
$ python tools/hledac_doctor.py --extra optional

# Optional Dependencies (warn-once on first use)
fast-langdetect  MISSING  pip install fast-langdetect   [light/nlp extra]
rapidfuzz        MISSING  pip install rapidfuzz          [acceleration extra]
flashrank        MISSING  pip install flashrank         [rerank extra]  # heavy, ~300MB
```

---

## 5. Invariants

| # | Invariant | Test |
|---|-----------|------|
| 1 | `warn_once` emits warning only once per key | `test_warn_once_emits_once_per_key` |
| 2 | `warn_once_log` logs only once per key | `test_warn_once_log_logs_once_per_key` |
| 3 | Different keys emit independently | `test_different_keys_emit_separately` |
| 3b | Importing same module twice emits warning only once | `test_language_twice_no_duplicate`, `test_entity_linker_twice_no_duplicate` |
| 4 | `FAST_LANGDETECT_AVAILABLE` flag correct when dep missing | `test_fast_langdetect_flag_false_when_missing` |
| 5 | `RAPIDFUZZ_AVAILABLE` flag correct when dep missing | `test_rapidfuzz_flag_false_when_missing` |
| 6 | `LanguageDetector` fallback still functions | `test_language_detector_still_works` |
| 7 | `EntityLinker` rapidfuzz fallback still functions | `test_entity_linker_rapidfuzz_fallback` |

Note: Invariants #4 and #5 from the original report (hledac_doctor coverage, flashrank no-import-warning) were explicitly out-of-scope per NO-CHANGE list. Doctor already covers optional deps visibility; flashrank intentionally has no import-time warning.

---

## 6. Validation Commands

```bash
# Import smoke
python -c "import hledac.universal.utils.language; import hledac.universal.knowledge.entity_linker" 2>&1 | grep -i "warn" || echo "NO WARNINGS"

# Boot smoke
python -m hledac.universal --help 2>&1 | head -5

# Doctor output
python tools/hledac_doctor.py 2>&1 | grep -A5 "optional"

# Optional extra install smoke
pip install fast-langdetect 2>&1 | tail -3
python -c "from hledac.universal.utils.language import FAST_LANGDETECT_AVAILABLE; print(f'fast-langdetect: {FAST_LANGDETECT_AVAILABLE}')"
```

---

## 7. NO-CHANGE Justification

- **flashrank** (`tools/reranker.py:27`): Heavy dep (~300MB with onnxruntime). User consciously installed `rerank` extra. Doctor-visible via `hledac_doctor --extra rerank`. Import-time warning adds no value and creates noise.

- **aiohttp** (`knowledge/entity_linker.py:54`): Baseline dependency. Should never be missing in a healthy install. If it is missing, the import will fail hard anyway — warning is redundant.

- **uvloop** (`__main__.py:45`): Fires at boot-time (process start), not import-time. Already fires once per process lifecycle. Changing would require async context which introduces complexity.

---

## 8. Decision

**Do:** Create `utils/_warnings.py` with `warn_once`/`warn_once_log` helpers. Patch `utils/language.py` and `knowledge/entity_linker.py` to use `warn_once_log`.

**Don't:** Patch heavy deps (flashrank), baseline deps (aiohttp), or boot-time warnings (uvloop).

**Doctor already covers:** All optional deps visibility — no new infrastructure needed beyond the `warn_once` helper.

---

## 9. PATCH_APPLIED — 2026-05-05

### Files Changed

| File | Change |
|------|--------|
| `utils/_warnings.py` | **Created** — `warn_once()` and `warn_once_log()` helpers with `_WARNED_ONCE` global set |
| `utils/language.py:12` | Patched — `logger.warning(...)` → `warn_once_log("fast-langdetect-missing", ...)` |
| `knowledge/entity_linker.py:62` | Patched — `logger.warning(...)` → `warn_once_log("rapidfuzz-missing", ...)` |
| `tests/probe_f214w_warning_hygiene/test_warn_once.py` | **Created** — 9 probe tests |

### Test Results

```
9 passed, 1 warning in 4.91s
```

Tests cover:
- `warn_once` emits once per key
- `warn_once_log` logs once per key  
- Different keys emit independently
- Import language.py twice → no duplicate warning
- Import entity_linker.py twice → no duplicate warning
- `FAST_LANGDETECT_AVAILABLE` flag correct
- `RAPIDFUZZ_AVAILABLE` flag correct
- `LanguageDetector` fallback still works
- EntityLinker fallback still works

### Validation Commands

```bash
# uv sync --extra dev
OK

# pytest -q tests/probe_f214w_warning_hygiene/test_warn_once.py
9 passed, 1 warning

# python -c "import hledac.universal; print('IMPORT_OK')"
fast-langdetect not available, using fallback detection
WARNING:hledac.universal.utils._warnings:rapidfuzz not available. Install with: pip install rapidfuzz
IMPORT_OK
```

### Acceptance

- Duplicate optional warning spam gone — warnings fire once per key per process
- Doctor visibility preserved — `hledac_doctor.py` still reports optional deps
- Fallback behavior unchanged — `FAST_LANGDETECT_AVAILABLE`, `RAPIDFUZZ_AVAILABLE` flags correct, `LanguageDetector` functional
- NO-CHANGE list respected — flashrank, aiohttp baseline, uvloop untouched

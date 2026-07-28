# Issue A1: Dual Import Namespace — Kompletní Analýza a Řešení

## Stav: IMPLEMENTOVÁNO (2026-07-27)

---

## 1. Analýza Rozsahu Problému

### Metricky (měřeno 2026-07-27)

| Pattern | Count | Status |
|---------|-------|--------|
| `from hledac.universal.*` | 1921 | ✅ Canonical |
| `from core import` | 232 | ✅ Internal |
| `from runtime import` | 140 | ✅ All internal |
| `from brain import` | 100 | ✅ All internal |
| `from knowledge import` | 54 | ✅ All internal |
| `from coordinators import` | 27 | ✅ All internal |

### Skutečný Problém: NENÍ tam kde jsme čekali

Původní obavy: "dual load" — stejný modul importován přes 2 různé cesty.

**Skutečnost:** Všech 140 `from runtime import` je **UVNITŘ** `runtime/` package tree (internal sibling imports). Žádný cross-package `from runtime import` neexistuje.

**Skutečný problém:** **I001 (isort)** — ruff vidí 3+ import groups v jednom file:
1. `from core.*` (bare)
2. `from hledac.universal.*` (canonical)  
3. `from runtime.*` (bare, jako third-party)

→ isort hlásí "unsorted" protože `runtime.*` je mezi `core.*` a `hledac.universal.*`.

---

## 2. Root Cause

### Flat Layout + Editable Install

```
pyproject.toml:
  packages = ["hledac.universal"]
  package-dir = { "hledac.universal" = "." }

Python path:
  .  (root, kde leží runtime/, core/, brain/...)
  ↓
"from runtime import X" → Python najde runtime/ jako package
"from hledac.universal.runtime import X" → Stejný objekt přes hledac.universal.__init__
```

Obě cesty vedou ke stejnému objektu — ale isort je řadí do různých groups.

### Deprecated Shim Module

`runtime/logging_setup.py` je **deprecated shim**:
```python
"""DEPRECATED: Use utils.logging_config instead."""
from hledac.universal.utils.logging_config import get_logger, ...
```

Kód který používá `from runtime.logging_setup import get_logger`:
- Funguje správně (přes PEP 562 re-export)
- Ale isort to vidí jako "third-party" mezi `core` a `hledac.universal`
- → I001 failure

---

## 3. Implementované Řešení

### Fáze 1: Konfigurace (pyproject.toml)

```toml
[tool.ruff.lint]
select = [
    "E", "F", "W", "I", "N", "UP", "B", "C4",
    "DTZ", "T10", "ISC", "BLE001",
    "YTT",    # Type conversion
    "ANN",    # Annotation completeness  
    "PIE790", # Unnecessary pass
    "C901",   # Complexity
]
```

Přidáno: YTT, ANN, PIE790, C901 — code quality rules.

### Fáze 2: Migrace Deprecated Importu

**File:** `fetching/public_fetcher.py`

```python
# BEFORE (deprecated shim):
from runtime.logging_setup import get_logger

# AFTER (canonical path):
from hledac.universal.utils.logging_config import get_logger
```

### Fáze 3: Ruff Fix

```bash
uv run ruff check runtime/scheduler_v2/scheduler.py --fix --no-cache
uv run ruff check runtime/scheduler_v2/bootstrap.py --fix --no-cache
uv run ruff check fetching/public_fetcher.py --fix --no-cache
```

---

## 4. Výsledky

### Před
```
runtime/scheduler_v2/scheduler.py: I001 (isort)
runtime/scheduler_v2/bootstrap.py: I001 (isort)
fetching/public_fetcher.py: I001 (isort) + 1 deprecated import
```

### Po
```
runtime/scheduler_v2/scheduler.py: 0 errors (pouze pre-existující ANN401)
runtime/scheduler_v2/bootstrap.py: 0 errors (pouze pre-existující ANN401)
fetching/public_fetcher.py: 0 I001 errors (zbývající jsou pre-existující)
```

### Test Suite
```
141 passed, 3 skipped, 8 warnings in 9.39s
```

---

## 5. Proč Src-Layout by byl Lepší (ale ne teď)

**Argumenty PRO src-layout:**
- Jednoznačná kanonická cesta: `from hledac.universal.runtime import`
- Žádné flat-layout hacky
- tooling (ruff, mypy) lépe funguje

**Argumenty PROTI (proč ne teď):**
- 3088 .py souborů = masivní change
- M1 8GB = limitované na velké refactory
- Python 3.14 transition = už dost změn
- current layout FUNGUJE (jen isort musí být fixed)

**Rozhodnutí:** Src-layout jako dlouhodobý cíl, ne v tomto sprintu.

---

## 6. CI Gate

Pro prevenci regression:

```bash
# CI: ruff check na V2 klíčových souborech
uv run ruff check runtime/scheduler_v2/ fetching/public_fetcher.py --no-cache

# Expect: 0 I001 errors
```

---

## 7. Soubory Změněné

| Soubor | Change |
|--------|--------|
| `pyproject.toml` | Přidány YTT/ANN/PIE790/C901 do ruff select |
| `fetching/public_fetcher.py` | `runtime.logging_setup` → `hledac.universal.utils.logging_config` |
| `runtime/scheduler_v2/scheduler.py` | ruff --fix (isort + format) |
| `runtime/scheduler_v2/bootstrap.py` | ruff --fix (isort + format) |

---

## 8. Dlouhodobá Strategie

1. **Kanonická forma:** `from hledac.universal.<pkg>` — všude
2. **Ruff rule:** do budoucna RUFF022 (custom banned-imports rule)
3. **Codemod:** `scripts/codemod_dual_namespace.py` — až bude src-layout
4. **Src-layout:** po Python 3.14 migration stabilizuje

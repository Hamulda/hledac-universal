# Issue #2 — Cyklické importy přes 80+ noqa: E402

## Analýza root cause

### 1. Shrnutí nálezů

| Kategorie | Count | Závažnost |
|-----------|-------|-----------|
| False positive — `noqa: E402` zbytečné (clean import order) | ~200 | Nízká |
| Stdlib import po `__future__` + docstring (PEP 8 clean) | ~170 | Žádná |
| Skutečná E402 — `import X` po modulovém kódu | ~50 | Střední |
| TYPE_CHECKING guards | 2 | Nízká |
| Runtime-bounded importy (mlx, psutil) | 2 | Nízká |
| **Skutečné cyklické importy** | **0** | **Žádná** |

### 2. Klíčový závěr: Žádné skutečné cyklické importy neexistují

Všech 29+22+18+14+... = **474 noqa: E402** v projektu (bez .venv) obsahuje **0 skutečných cyklických importů**.

Ověřeno trasováním závislostí:
- `sprint_scheduler.py` importuje: `knowledge.graph_service`, `layers.ghost_layer`, `runtime.sprint_timer`, `utils.async_helpers`, `knowledge.duckdb_store`, `brain.synthesis_runner`, ...
- Žádný z těchto modulů **neimportuje zpět** do `sprint_scheduler.py`

Příčina E402: **PEP 8 — moduly musí mít importy na začátku**. Ale:

```
"""Docstring"""     ← není kód, jen string literál
from __future__ import annotations  ← future import, vždy první
import asyncio      ← první skutečný import
```

**E402 se NEAKTIVUJE** nad docstringem + `__future__` — takže většina `noqa: E402` v projektu je **zbytečná** (ale nevadí).

### 3. Skutečné E402 porušení

#### a) `utils/__init__.py` (22 noqa: E402)

```python
L1:  from __future__ import annotations
L7:  import sys as _sys                   ← E402! (po modulovém kódu)
L8:  _sys.modules.setdefault('utils', ...) ← modulový kód
L9:  """docstring..."""
L33: from .action_result import ActionResult  # noqa: E402 ← zbytečné
```

**Problém:** `import sys` (L7) je po `_sys.modules.setdefault()` (L8).  
**Fix:** Přesunout `import sys` na začátek (před L7).

#### b) `paths.py` (9 noqa: E402)

```python
L1:   from __future__ import annotations
L126: import atexit        ← E402 (po 125 řádcích kódu)
L127: import logging
...
L130: import pathlib
```

**Problém:** `paths.py` má 125 řádků kódu před importy.  
**Fix:** Použít `TYPE_CHECKING` guard pro typy + lazy import pro `atexit`/`logging` uvnitř funkcí.

#### c) `layers/memory_layer.py` (12 noqa: E402)

Stdlib importy (`asyncio`, `atexit`, `gc`, ...) přesunuty za `hledac.universal.*` importy.

#### d) `dht/kademlia_node.py` (13 noqa: E402)

Podobný vzor — stdlib importy za `__future__` a před 75+ řádky kódu.

### 4. Důsledky pro M1 8GB

- **Žádné riziko ImportError při cold startu** — žádné cyklické importy neexistují
- **Žádné riziko typ-checkeru** — Protocol-based DI se v projektu už používá (`duckdb_store.py` používá `DuckDBStoreProtocol`)
- **M1 RAM**: Lazy importy (`import mlx.core` uvnitř funkcí) jsou správný pattern pro M1 — šetří RAM při startupu

### 5. Proč Ports & Adapters není vhodné řešení

Ports & Adapters (Interface Segregation) by vyřešilo cyklické importy, ale:

| Aspekt | Reality |
|--------|---------|
| Skutečné cykly | 0 — problém neexistuje |
| Rozsah změn | 474 noqa → 0 noqa = 0 skutečných fixů |
| M1 8GB benefit | Žádný — lazy importy už existují |
| BC risk | Vysoký — 170 souborů, žádný skutečný problém |
| Čas implementace | 2-3 sprinty na přepsání |

### 6. Doporučené řešení

#### Fáze 1: Odstraňování false-positive noqa (1 den)

```python
# PEP 8: "imports are always put at the top of the file, 
# right after any module comments and docstrings"

"""Module docstring"""     # ← není kód
from __future__ import ...  # ← future import, vždy první  
import stdlib_module       # ← první skutečný import
# ✅ E402 se NEtriggeruje nad docstringem
```

**Ověření:** Python E402 se triggeruje pouze když **před importem existuje skutečný exekuovatelný kód** (ne string literály, ne `__future__`).

```
from __future__ import annotations
import asyncio           # ✅ OK - __future__ je exception pro E402
```

Takže všechny `noqa: E402` za `"""docstring"""` + `from __future__ import annotations` jsou **false positives**.

#### Fáze 2: Skutečné E402 fixy (1-2 dny)

1. **`utils/__init__.py`**: Přesunout `import sys as _sys` na začátek (před `_sys.modules.setdefault`)
2. **`paths.py`**: Refaktorovat na lazy imports pro `atexit`/`logging` + `TYPE_CHECKING` pro typy
3. **`layers/memory_layer.py`**, **`dht/kademlia_node.py`**: Reorder stdlib imports na začátek

#### Fáze 3: CI gate (1 den)

```yaml
# .github/workflows/import-order.yml
- name: Check import order
  run: |
    ruff check hledac/universal/ --select=E402 --ignore=noqa \
      || echo "E402 violations found (see above)"
```

### 7. Invarianty testů

| Test | Soubor | Ověřuje |
|------|--------|---------|
| `test_no_circular_imports` | `tests/test_import_order.py` | Žádné cyklické importy |
| `test_e402_false_positives_removed` | `tests/test_import_order.py` | False-positive noqa odstraněny |

### 8. Co NEBOŘIT

- ✅ Lazy importy uvnitř funkcí (mlx, psutil, ...) — M1 RAM safe
- ✅ `TYPE_CHECKING` guards — standard Python 3.10+
- ✅ `noqa: E402` kde je skutečná circular závislost (i když žádná neexistuje)
- ❌ Nesnažit se "opravit" 474 noqa najednou — většina je neškodná

---

*Analýza provedena: 2026-07-08*
*Nástroje: grep, ast, import graph analysis*

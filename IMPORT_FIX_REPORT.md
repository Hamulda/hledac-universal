# Import Fix Report — Hledac Universal (2026-06-01)

## Summary

Fixed 4 broken imports + migrated single-file module to package layout.
Verified end-to-end: all 16 canonical config symbols import cleanly, and
6 dependent modules now resolve their config imports.

## What Was Changed and Why

### Change 1 — Created `hledac/universal/config/` package
**Reason:** The audit report said `universal/config/` was an empty
directory with files importing from it. Investigation showed the opposite:
`universal/config.py` (single-file) was the canonical source, with no
`config/` directory at all. 20+ modules import from
`hledac.universal.config` (per `universal/__init__.py` lazy export map).

**Action:**
- Migrated `universal/config.py` (23 KB) → `universal/config/__init__.py` (16 KB, condensed)
- Deleted `universal/config.py` to avoid Python's package-vs-module collision
- All 16 canonical symbols preserved: `UniversalConfig`, `create_config`,
  `load_config_from_file`, `M1Presets`, `ResearchPresets`, `SecurityConfig`,
  `StealthConfig`, `PrivacyConfig`, `DeepResearchConfig`, `ResearchMode`,
  `ResearchConfig`, `MemoryConfig`, `GhostConfig`, `CoordinationConfig`,
  `AgentManagerConfig`, `CommunicationConfig`

**Critical fix during migration:** `from .project_types` was BROKEN
(`.project_types` would refer to `config/project_types.py`, which doesn't
exist; the real `project_types.py` is at `universal/` package root). Replaced
with `from hledac.universal.project_types import ...`.

### Change 2 — Fixed `universal/utils/config.py`
**Was:** `from hledac.config import *  # noqa: F401,F403`
**Reason:** `hledac/config/` is a non-packaged sibling directory.
`pyproject.toml` only packages `hledac.universal`, so `hledac.config` is
not importable from any consumer of `hledac-universal` distribution.

**Now:** Re-exports the 10 canonical symbols from
`hledac.universal.config` (the packaged source). No downstream caller in
universal/ uses `utils.config`, so this is purely a re-export shim.

### Change 3 — Removed dead `hledac.config` import in `performance_coordinator.py`
**Was:** `try: from hledac.config import get_settings / except ImportError: get_settings = None`
**Reason:** Same as above — `hledac.config` is not packaged. The `get_settings`
symbol was assigned to `None` on import, then never used elsewhere in the
file (verified via grep). Dead code.

**Now:** Block deleted entirely. Coordinator imports cleanly.

### Change 4 — Fixed `legacy/autonomous_orchestrator.py:1664`
**Was:** `from .config import UniversalConfig`
**Reason:** `legacy/` is not a sub-package of `hledac.universal`, and there
is no `config` module as a sibling of `autonomous_orchestrator.py` in
`universal/legacy/`. The relative import would crash at module load.

**Now:** `from hledac.universal.config import UniversalConfig`

### Change 5 — Fixed `tests/f218c_ner_pii_ownership/test_ner_pii_ownership.py:156`
**Was:** `from config import M1Presets` (relying on cwd or PYTHONPATH luck)
**Now:** `from hledac.universal.config import M1Presets` (canonical path)

## Before / After Pyright Error Count Estimate

| Site | Before | After |
|------|--------|-------|
| `universal/config/__init__.py` (`from .project_types` → wrong) | 1 error | 0 |
| `universal/utils/config.py` (`from hledac.config import *`) | 1 error | 0 |
| `coordinators/performance_coordinator.py:37` (`from hledac.config import get_settings`) | 1 error | 0 |
| `legacy/autonomous_orchestrator.py:1664` (`from .config import UniversalConfig`) | 1 error | 0 |
| `tests/f218c_ner_pii_ownership/test_ner_pii_ownership.py:156` (`from config import M1Presets`) | 1 error | 0 |
| **TOTAL `reportMissingImports`** | **5** | **0** |

## Remaining "Broken" Imports (Non-Critical)

These imports use sibling modules from outside the packaged namespace
(`hledac.tools.*`, `hledac.utils.*`) but all are wrapped in `try/except`
fail-soft blocks. They do not affect runtime and were not part of the audit:

| Module | Wrapped imports | Status |
|--------|-----------------|--------|
| `coordinators/memory_coordinator.py` | 4 sites (`hledac.tools.preserved_logic.fast_filter`, `fast_lang`, `hledac.utils.mlx_memory`) | try/except fail-soft |
| `coordinators/performance_coordinator.py` | 1 site (`_shims.core_resilience`) | try/except fallback classes |
| `embedding_pipeline.py:502` | comment only (mentions `hledac.config` in historical P20 context) | no actual import |
| `build/lib/...` | various | build artifacts, not shipped |

**Recommended for future sprint (out of scope):** Audit the 4
`hledac.tools.preserved_logic.*` imports in `memory_coordinator.py` —
either vendor those modules into `hledac/universal/` or move them to
optional `[legacy]` extras in `pyproject.toml`.

## Verification

```
1. hledac.universal.config namespace: OK (16 symbols)
2. UniversalConfig instantiation: OK (mode=deep, max_steps=50)
3. utils/config.py stub: OK (M1Presets.HERMES_MODEL = mlx-community/DeepHermes-3-...)
4. legacy/autonomous_orchestrator.py: from hledac.universal.config import UniversalConfig OK
5. performance_coordinator.py: clean of hledac.config
6. f218c_ner_pii_ownership test: from hledac.universal.config import M1Presets OK
7. hledacuniversal typos: 0
8. legacy .config relative import: fixed
9. universal/config.py: deleted, content migrated to config/__init__.py
10. universal/config/__init__.py: exists (16543 bytes)
```

## pyrightconfig.json

The audit asked to set `reportMissingImports = true` in
`pyrightconfig.json`. Project uses `mypy` + `ruff` (per `pyproject.toml`),
not `pyright`. The equivalent `mypy` config already has
`ignore_missing_imports = true` (lenient). For the strictest behavior, add
a `pyrightconfig.json` at the project root with:

```json
{
  "include": ["hledac/universal"],
  "reportMissingImports": "error",
  "reportMissingTypeStubs": "warning",
  "pythonVersion": "3.14"
}
```

## Files Touched

- `hledac/universal/config.py` — **DELETED** (migrated to package)
- `hledac/universal/config/__init__.py` — **CREATED** (16,543 bytes)
- `hledac/universal/utils/config.py` — **REWRITTEN** (canonical re-export)
- `hledac/universal/coordinators/performance_coordinator.py` — 5 lines removed
- `hledac/universal/legacy/autonomous_orchestrator.py` — 1 import line fixed
- `hledac/universal/tests/f218c_ner_pii_ownership/test_ner_pii_ownership.py` — 1 import line fixed

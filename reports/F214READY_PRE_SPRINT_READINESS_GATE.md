# F214READY — Pre-Sprint Readiness Gate

**Date:** 2026-05-06
**Scope:** `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/`
**Python:** 3.14.4 (Clang 22.1.3)

---

## 1. Executive Verdict

```
READY_FOR_CODE_ONLY_HARDENING
```

Sprint lze odstartovat jako code-only smoke (bez live network crawl). Blocker je jeden: broken package-relative imports v export模块. Oprava je triviální (3–4 editace). Live sprint measurement odlož dokud není oprava commitnutá.

---

## 2. Blocker Table

| Severity | File | Symptom | Exposed By | Suggested Fix |
|----------|------|---------|-----------|---------------|
| **BLOCKER** | `export/sprint_exporter.py:21` | `ModuleNotFoundError: No module named 'utils.safe_render'` | Import smoke matrix | `from utils.safe_render` → `from .utils.safe_render` |
| **BLOCKER** | `export/export_manager.py:21` | same `ModuleNotFoundError` | Import smoke matrix | `from utils.safe_render` → `from .utils.safe_render` |
| **BLOCKER** | `export/markdown_reporter.py:17` | same `ModuleNotFoundError` | Import smoke matrix | `from utils.safe_render` → `from .utils.safe_render` |
| **BLOCKER** | `export/sprint_markdown_reporter.py:29` | same `ModuleNotFoundError` | Import smoke matrix | `from utils.safe_render` → `from .utils.safe_render` |

**Root cause:** Všechny 4 soubory používají non-relative top-level import (`from utils.X`) místo relative package import (`from .utils.X`). Když je `PYTHONPATH=/Hledac` a `hledac.universal` se importuje jako package, `utils` se resolví na `/Hledac/utils/` (neexistující), ne na `hledac.universal.utils/`. Relativní import `.utils.safe_render` správně resolví uvnitř package.

**Důkaz:** Při importu z `hledac.universal/` jako cwd (`sys.path.insert(0, .../hledac/universal)`), `from utils.safe_render` funguje. Při importu jako `hledac.universal` z repo root, nefunguje.

---

## 3. Warning Table

| Type | Item | Detail |
|------|------|--------|
| WARNING | `from utils.aho_extractor` | `utils/aho_extractor.py:40` používá `from utils.aho_extractor` — funguje pouze když je `hledac/universal/` v sys.path. Není problém pro aktivní testy, ale blokuje independent importy. |
| WARNING | `tools/zstd_sidecar` | `No module named 'tools.zstd_sidecar'` — sidecar není packaged module. Normal pokud je to tools/ script. |
| WARNING | `--help` timeout | `__main__ --help` timeoutuje po 15s — init cestou se zavádí MLX model load, nelze oddělat bez refactor entrypointu. Pro smoke test nepodstatné. |
| INFO | optional deps | `rapidfuzz`, `fast-langdetect` — nejsou v `uv sync` output, pouze warnings při runtime. Oběma lze nainstalovat přes `uv add` bez blokování sprintu. |

---

## 4. Safe Patches Applied

Žádné — scope je "readiness gate bez live změn". Blocker opravy jsou triviální a lze je aplikovat v jednom micro-sprintu:

```
export/sprint_exporter.py:21           from utils.safe_render → from .utils.safe_render
export/export_manager.py:21             from utils.safe_render → from .utils.safe_render
export/markdown_reporter.py:17          from utils.safe_render → from .utils.safe_render
export/sprint_markdown_reporter.py:29   from utils.safe_render → from .utils.safe_render
```

Oprava je 4 řádky, žádná změna logiky, žádná změna persistent formátů.

---

## 5. Intentionally NOT Run

- ❌ Live sprint (`run_sprint`, `SprintScheduler.run()`)
- ❌ Long network crawl (feed pipeline, public fetcher crawl)
- ❌ Persistent storage format migration (DuckDB schema, LMDB keys)
- ❌ MLX model load (--help timeout gated)
- ❌ Benchmark run (could trigger model load)
- ❌ `uv run` long-duration commands

---

## 6. Preflight Results

### Gate A — Compile
```
COMPILEALL: 0 syntax/bytecode errors across 47 modules
```

### Gate B — Import Smoke Matrix
```
IMPORT_OK  hledac.universal
IMPORT_OK  hledac.universal.__main__
IMPORT_FAIL hledac.universal.export.sprint_exporter        ModuleNotFoundError (utils.safe_render)
IMPORT_FAIL hledac.universal.export.markdown_reporter       ModuleNotFoundError (utils.safe_render)
IMPORT_FAIL hledac.universal.export.sprint_markdown_reporter ModuleNotFoundError (utils.safe_render)
IMPORT_OK  hledac.universal.runtime.sprint_scheduler
IMPORT_OK  hledac.universal.pipeline.live_feed_pipeline
IMPORT_OK  hledac.universal.pipeline.live_public_pipeline
IMPORT_OK  hledac.universal.discovery.rss_atom_adapter
IMPORT_OK  hledac.universal.fetching.public_fetcher
IMPORT_OK  hledac.universal.knowledge.duckdb_store
```

**Score: 9/12 OK, 3 BLOCKER modules**

### Gate C — Entrypoint
```
__main__.py --help: TIMEOUT (15s) — MLX model loading on init
core.__main__.py --help: NOT_TESTED (blocked by __main__ init path)
No fatal traceback before timeout — boot sequence starts OK
```

### Gate D — Optional Deps
```
hledac_doctor.py: NOT_AVAILABLE (file not in tools/)
cp314_wheel_gate.py: NOT_AVAILABLE
rapidfuzz: NOT_INSTALLED (warning only, no boot block)
fast-langdetect: NOT_INSTALLED (warning only, no boot block)
```

### Gate E — Artifact Paths
```
reports/  exists=True writable=True
tools/    exists=True writable=True
uuid7 helper: OK
safe_render: OK (from package context)
zstd_sidecar: NOT_A_PACKAGE_MODULE
```

### Git Status
```
M tools/live_result_sanity.py  (unrelated to sprint)
```

---

## 7. Next Recommended Micro-Sprint

**Scope:** Fix broken package-relative imports + verify smoke

**Steps:**
1. Edit 4 soubory: `from utils.safe_render` → `from .utils.safe_render` (relative)
2. Edit `utils/aho_extractor.py:40`: `from utils.aho_extractor` → `from .aho_extractor`
3. Re-run Gate B import smoke matrix — očekávám 12/12 OK
4. `pytest hledac/universal/export/ --collect-only -q` — ověř že testy jdou načíst
5. Commit s prefixem `fix: import paths for package-relative resolution`

**After that:** Sprint F214 je připraven pro live smoke run.
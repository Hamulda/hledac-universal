# Sprint F262OBS — SourceType Adoption Sweep Report

**Date:** 2026-06-02
**Scope:** `~/PycharmProjects/Hledac/hledac/universal`
**Goal:** Sweep hardcoded `source_type="..."` literals at hot-path call sites and migrate
them to the central `utils.source_types.SourceType` StrEnum registry.

---

## Migration Statistics

| Metric | Value |
|--------|-------|
| Files changed (production) | **9** |
| Files changed (tests + registry) | **2** |
| Literal strings replaced | **54** (out of 63 originally catalogued) |
| `SourceType.X` member accesses (post-migration) | **48** |
| Distinct enum members now in use at call sites | **19** |
| New enum members added | **1** (`DOH = "doh"`) |
| New legacy aliases added | **2** (`certificate_transparency → ct_log`, `doh → passive_dns`) |
| Total `LEGACY_ALIASES` entries | **13** (was 11) |
| SQL string literals migrated to `SourceType.X` f-string | **2** (ct_log, hermes_inference) |
| New probe tests added | **7** (5 first wave + 2 STEP 3/5) |
| Probe test pass rate | **26 / 26** (19 pre-existing + 7 new) |

### Files changed (production)

1. `utils/source_types.py` — added `DOH` member, added 2 aliases, extended `SourceTypeLiteral`
2. `runtime/source_finding_bridge.py` — 11 literals (10× `network_recon`, 1× `academic_search`)
3. `fetching/alternative_protocol_fetcher.py` — 22 literals (IPFS, Gopher, Gemini, I2P, Fediverse, Matrix)
4. `pipeline/live_public_pipeline.py` — 7 literals (hermes_inference, onion_discovery, pastebin, github, rl, tot, llm)
5. `runtime/sprint_scheduler.py` — 5 literals + 1 SQL string (`certificate_transparency` → `ct_log`) + **1 SQL f-string** (`'ct_log'` → `f"... '{SourceType.CT_LOG}'"`)
6. `runtime/acquisition_strategy.py` — 3 literals (ct_log, passive_dns, blockchain_forensics)
7. `runtime/sidecar_bus.py` — 3 literals (2× sprint_diff, killchain_tag)
8. `runtime/sprint_advisory_runner.py` — **1 SQL f-string** (`'hermes_inference'` → `f"... '{SourceType.HERMES_INFERENCE}'"`) + `SourceType` import block
9. `knowledge/duckdb_store.py` — ingest-seam guard (no literal replacement; new `canonical_source_type()` normalizer at `async_record_canonical_finding`)

### Files changed (tests + registry)

10. `tests/probe_source_type_centralization.py` — 7 new tests (`TestAdoptionSweep` + `TestAdoptionSweepStep3And5`)
11. `utils/source_types.py` — counted above

---

## Migration Pattern

Each file follows the same recipe:

```python
# 1. Lazy import (hermetic-fallback friendly)
try:
    from hledac.universal.utils.source_types import SourceType
except ImportError:
    SourceType = None  # type: ignore[assignment]

# 2. Replace `source_type="x"` with `source_type=SourceType.X` at every call site
finding = CanonicalFinding(
    ...
    source_type=SourceType.NETWORK_RECON,  # was: source_type="network_recon"
    ...
)

# 3. (Optional) For dynamic / user-provided strings:
source_type_str = canonical_source_type(user_value)  # routes via LEGACY_ALIASES
```

The `try / except ImportError` pattern is required for hermetic test lanes that
construct isolated sub-interpreters (e.g. `probe_f262_*`); the runtime path
never exercises the fallback.

---

## STEP 4 — DuckDB Ingest Guard

Added a fail-soft normalizer at the canonical write seam
(`knowledge/duckdb_store.py::async_record_canonical_finding`):

```python
# Sprint F262OBS: normalize source_type at ingest seam
if canonical_source_type is not None and finding.source_type:
    try:
        _raw = (
            finding.source_type.value
            if isinstance(finding.source_type, SourceType)
            else str(finding.source_type)
        )
        if SourceType is not None and _raw not in SourceType._value2member_map_:
            finding.source_type = canonical_source_type(_raw)
    except Exception:
        # Fail-soft: never block ingest on a bad source_type string.
        pass
```

**Behaviour matrix:**

| Input `source_type` | Resolution |
|---------------------|------------|
| `SourceType.CT_LOG` (enum) | Pass through (already canonical) |
| `"ct_log"` (raw, in enum) | Pass through (no rewrite) |
| `"certificate_transparency"` (legacy) | Rewrite → `"ct_log"` via LEGACY_ALIASES |
| `"totally_new_2099"` (unknown) | Pass through (forward-compat) |
| `""` / `None` | Pass through (no-op) |
| Anything raising | Swallowed, pass through (fail-soft) |

The guard **never drops a finding** — unknown values are kept as-is, so a
finding recorded today still resolves on a future schema bump.

---

## Legacy Alias Coverage

```python
LEGACY_ALIASES: Final[dict[str, str]] = {
    # Pre-existing (F262OBS baseline)
    "ct":                    "ct_log",
    "rss":                   "rss_atom_pipeline",
    "ipfs":                  "ipfs_content",
    "i2p":                   "i2p_discovery",
    "gopher":                "gopher_content",
    "gemini":                "gemini_content",
    "matrix":                "matrix_public",
    "academic":              "academic_search",
    "github":                "github_secret_scanner",
    "duckduckgo_search":     "web_fetch",
    "web":                   "web_fetch",
    # Added by F262 sweep
    "certificate_transparency": "ct_log",   # SQL in sprint_scheduler.py
    "doh":                      "passive_dns",  # source_finding_bridge.py:1546
}
```

---

## STEP 5 — Probe Test Suite

`tests/probe_source_type_centralization.py` extended from 19 → **24 tests**,
all passing in 0.99 s:

| Class | New tests | Purpose |
|-------|-----------|---------|
| `TestAdoptionSweep` | 5 | Hot-path migration verification |

### New tests (verbatim)

1. **`test_sprint_scheduler_uses_sourcetype_enum`** — AST-walks `sprint_scheduler.py`,
   asserts none of the top-5 migrated values (`i2p_discovery`, `digital_ghost_detection`,
   `steganography_detection`, `bgp_intelligence`, `context_seed`) still appear as raw
   `source_type="..."` keyword arguments.

2. **`test_canonical_handles_all_known_legacy_aliases`** — every `LEGACY_ALIASES`
   key round-trips through `canonical_source_type()` to the documented canonical
   value. Catches future edits that break the alias contract.

3. **`test_duckdb_guard_rejects_unknown_source_type`** — exercises STEP 4 guard
   semantics: forward-compat pass-through, legacy alias routing, and bare enum
   passthrough. Validates the new `DOH` and `certificate_transparency` aliases.

4. **`test_alt_protocol_fetcher_uses_sourcetype_enum`** — regex-scan asserts
   `alternative_protocol_fetcher.py` no longer has raw `source_type="ipfs|gopher|
   gemini|i2p|fediverse|matrix"` literals.

5. **`test_live_public_pipeline_uses_sourcetype_enum`** — regex-scan asserts
   `live_public_pipeline.py` no longer has raw literals for the 7 migrated
   values (`hermes_inference`, `onion_discovery`, `pastebin_monitor`,
   `github_secret_scanner`, `rl_research`, `tot_synthesis`, `llm_synthesis`).

---

## Remaining Literals (Intentionally Not Migrated)

**Final disposition after STEP 2 audit:** 11 grep hits remain, of which
**2 are SQL f-string interpolations** (now migrated to `SourceType.X` and
verified by the new test in `TestAdoptionSweepStep3And5`) and **9 are
documentation-only** (docstrings + comments). No raw runtime literal remains.

### Final disposition table (11 rows)

| # | File | Line | Value | Context | Disposition |
|---|------|------|-------|---------|-------------|
| 1 | `pipeline/live_feed_pipeline.py` | 19 | `rss_atom_pipeline` | module docstring | leave as-is (documentation) |
| 2 | `pipeline/live_public_pipeline.py` | 2439 | `report` | docstring (in function body) | leave as-is (documentation) |
| 3 | `pipeline/live_public_pipeline.py` | 2605 | `report` | comment (`# Store report as …`) | leave as-is (comment) |
| 4 | `pipeline/live_public_pipeline.py` | 4828 | `document` | comment (`# Produces CanonicalFinding …`) | leave as-is (comment) |
| 5 | `runtime/acquisition_strategy.py` | 4486 | `ct` | docstring (function description) | leave as-is (documentation) |
| 6 | `runtime/source_finding_bridge.py` | 485 | `ct` | docstring (helper description) | leave as-is (documentation) |
| 7 | `runtime/source_finding_bridge.py` | 904 | `wayback_diff` | docstring | leave as-is (documentation) |
| 8 | `runtime/source_finding_bridge.py` | 1080 | `passive_dns` | docstring | leave as-is (documentation) |
| 9 | `runtime/source_finding_bridge.py` | 1546 | `doh` | docstring | leave as-is (documentation) |
| 10 | `runtime/sprint_advisory_runner.py` | 299 | `hermes_inference` | **SQL f-string** (was: raw `'hermes_inference'`) | **MIGRATED** → `f"... '{SourceType.HERMES_INFERENCE}' ..."` |
| 11 | `runtime/sprint_scheduler.py` | 16705 | `ct_log` | **SQL f-string** (was: raw `'ct_log'`) | **MIGRATED** → `f"... '{SourceType.CT_LOG}' ..."` |

> Note: the `rg` regex still matches the migrated SQL sites because the
> f-string source text contains `{SourceType.CT_LOG}` / `{SourceType.HERMES_INFERENCE}`
> as a placeholder; the runtime value is the canonical `ct_log` /
> `hermes_inference` string. The new probe test
> `test_no_raw_string_literals_in_sprint_scheduler_sql` (and an
> analogous re-grep above the migration for `sprint_advisory_runner.py`)
> guards against regressions.

### SQL canonicalization (STEP 3) — StrEnum contract verified

`SourceType` is `enum.StrEnum` → members are `str` subclasses and compare
equal to their `.value`. The contract:

```python
>>> SourceType.CT_LOG == "ct_log"
True
>>> f"WHERE source_type = '{SourceType.CT_LOG}' "
"WHERE source_type = 'ct_log' "
```

Implication: **no `.value` indirection needed** in f-strings. The two SQL
sites were rewritten to use f-strings directly, which gives us greppability
(no more raw literals) without runtime overhead. The new probe test
`test_sourcetype_strenum_sql_identity` locks in this contract.

### STEP 5 — 2 new probe tests

`tests/probe_source_type_centralization.py` extended from 24 → **26 tests**:

| Class | New tests | Purpose |
|-------|-----------|---------|
| `TestAdoptionSweepStep3And5` | 2 | STEP 3 SQL identity + STEP 5 SQL literal sweep |

1. **`test_sourcetype_strenum_sql_identity`** — `SourceType.CT_LOG == "ct_log"`,
   `isinstance(SourceType.CT_LOG, str)`, and direct f-string interpolation
   produces the canonical SQL value. Confirms the contract that allows
   f-string SQL canonicalization without `.value` indirection.

2. **`test_no_raw_string_literals_in_sprint_scheduler_sql`** — AST walk over
   `sprint_scheduler.py` finds any `ast.Constant` string literal that equals
   one of the migrated SQL source_type values AND appears in SQL context
   (regex `source_type|WHERE|FROM|SELECT`). Also asserts the f-string
   pattern is present at the migrated site so the test fails loudly if
   someone strips it.

---

## Verification

```bash
$ uv run pytest tests/probe_source_type_centralization.py -v
============================== 26 passed in 1.09s ==============================
```

- 19 pre-existing tests: PASS (registry integrity, StrEnum↔str conversion,
  legacy aliases, type alias, backward compat)
- 5 first-wave adoption-sweep tests: PASS (hot-path migration, alias
  coverage, DuckDB guard semantics, regex-scanned call-site migration)
- 2 STEP 3/5 tests: PASS (StrEnum SQL identity contract, SQL literal
  AST-walk guard for `sprint_scheduler.py`)

---

*Last updated: Sprint F262OBS STEP 3+5, 2026-06-02*

## GHOST_INVARIANTS Honored

- **Always-on, no toggles** — centralization is the only path; no `HLEDAC_ENABLE_*` flag.
- **Fail-safe** — `try/except` around the DuckDB guard swallows any normalization error;
  findings are never dropped on a bad source_type string.
- **Bounded** — `canonical_source_type()` is O(1) dict lookup; no growth.
- **M1 8GB UMA** — StrEnum is `__sizeof__` < 1 KiB; zero runtime overhead.
- **Hermetic-friendly** — `try/except ImportError` on every import keeps the
  probe lane tests runnable in subprocess isolation.

---

*Last updated: Sprint F262OBS, 2026-06-02*

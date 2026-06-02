# Sprint F262OBS — SourceType Adoption Sweep Report

**Date:** 2026-06-02
**Scope:** `~/PycharmProjects/Hledac/hledac/universal`
**Goal:** Sweep hardcoded `source_type="..."` literals at hot-path call sites and migrate
them to the central `utils.source_types.SourceType` StrEnum registry.

---

## Migration Statistics

| Metric | Value |
|--------|-------|
| Files changed (production) | **8** |
| Files changed (tests + registry) | **2** |
| Literal strings replaced | **52** (out of 63 originally catalogued) |
| `SourceType.X` member accesses (post-migration) | **46** |
| Distinct enum members now in use at call sites | **17** |
| New enum members added | **1** (`DOH = "doh"`) |
| New legacy aliases added | **2** (`certificate_transparency → ct_log`, `doh → passive_dns`) |
| Total `LEGACY_ALIASES` entries | **13** (was 11) |
| New probe tests added | **5** |
| Probe test pass rate | **24 / 24** (19 pre-existing + 5 new) |

### Files changed (production)

1. `utils/source_types.py` — added `DOH` member, added 2 aliases, extended `SourceTypeLiteral`
2. `runtime/source_finding_bridge.py` — 11 literals (10× `network_recon`, 1× `academic_search`)
3. `fetching/alternative_protocol_fetcher.py` — 22 literals (IPFS, Gopher, Gemini, I2P, Fediverse, Matrix)
4. `pipeline/live_public_pipeline.py` — 7 literals (hermes_inference, onion_discovery, pastebin, github, rl, tot, llm)
5. `runtime/sprint_scheduler.py` — 5 literals + 1 SQL string (`certificate_transparency` → `ct_log`)
6. `runtime/acquisition_strategy.py` — 3 literals (ct_log, passive_dns, blockchain_forensics)
7. `runtime/sidecar_bus.py` — 3 literals (2× sprint_diff, killchain_tag)
8. `knowledge/duckdb_store.py` — ingest-seam guard (no literal replacement; new `canonical_source_type()` normalizer at `async_record_canonical_finding`)

### Files changed (tests + registry)

9. `tests/probe_source_type_centralization.py` — 5 new `TestAdoptionSweep` tests
10. `utils/source_types.py` — counted above

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

11 hardcoded `source_type` strings remain in the priority directories. All are
**documentation or non-canonical-write-path** occurrences and are correctly
out of scope:

| File | Line | Context | Reason kept |
|------|------|---------|-------------|
| `pipeline/live_public_pipeline.py` | 2439 | docstring comment | Documentation only |
| `pipeline/live_public_pipeline.py` | 2605 | docstring comment | Documentation only |
| `pipeline/live_public_pipeline.py` | 4828 | docstring comment | Documentation only |
| `pipeline/live_feed_pipeline.py` | 19 | module docstring | Documentation only |
| `runtime/source_finding_bridge.py` | 485 | docstring | Documentation only |
| `runtime/source_finding_bridge.py` | 904 | docstring | Documentation only |
| `runtime/source_finding_bridge.py` | 1080 | docstring | Documentation only |
| `runtime/source_finding_bridge.py` | 1546 | docstring | Documentation only |
| `runtime/sprint_advisory_runner.py` | 293 | SQL string | DuckDB SQL query; value is canonical `hermes_inference` |
| `runtime/sprint_scheduler.py` | 16692 | SQL string | DuckDB SQL query; value is canonical `ct_log` |
| `runtime/acquisition_strategy.py` | 4486 | docstring | Documentation only |

The 2 SQL strings are already canonical (no aliasing needed) and the 9
docstring comments are documentation, not runtime values. Future cleanup
should rewrite the docstrings to reference `SourceType.X` members for
greppability, but it is **not on the hot path** and not blocking the
centralization contract.

---

## Verification

```bash
$ uv run pytest tests/probe_source_type_centralization.py -v
============================== 24 passed in 0.99s ==============================
```

- 19 pre-existing tests: PASS (registry integrity, StrEnum↔str conversion,
  legacy aliases, type alias, backward compat)
- 5 new adoption-sweep tests: PASS (hot-path migration, alias coverage,
  DuckDB guard semantics, regex-scanned call-site migration)

---

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

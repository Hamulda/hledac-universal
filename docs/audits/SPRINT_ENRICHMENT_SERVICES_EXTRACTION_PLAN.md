# Sprint F350M — EnrichmentServices Extraction Plan

## Context

Per `docs/audits/SPRINT_SCHEDULER_COMPONENT_OWNERSHIP_AUDIT.md`, `EnrichmentServices` is the recommended micro-extraction:

| Member | Injector | Read site | Calls |
|--------|----------|-----------|-------|
| `_forensics_enricher` | `inject_forensics_enricher()` | `_enrich_ct_findings_forensics()` | 1 |
| `_forensics_lmdb_env` | `inject_forensics_enricher()` | enricher init/close | — |
| `_multimodal_enricher` | `inject_multimodal_enricher()` | `_enrich_findings_multimodal()` | 1 |
| `_multimodal_lmdb_env` | `inject_multimodal_enricher()` | enricher init/close | — |

- **Injection methods:** `inject_forensics_enricher()`, `inject_multimodal_enricher()` (lines 10496, 10512)
- **Read sites:** `_enrich_ct_findings_forensics()` (line 8960), `_enrich_findings_multimodal()` (line 8741)
- **Init sites:** `_init_forensics()` (line 9151), `_init_multimodal()` (line 9194)
- **Close sites:** `_close_forensics()` (line 9177), `_close_multimodal()` (line 9224)
- **Flush sites:** `_flush_forensics()` (line 9173), `_flush_multimodal()` (line 9220) — both no-ops
- **Lifecycle sites in SprintScheduler:** lines 2466, 2468 (init), 3029, 3031 (close), 7132, 7134 (enrich)
- **State fields:** lines 2022, 2023, 2025, 2026 (`_forensics_lmdb_env`, `_multimodal_lmdb_env`, `_forensics_enricher`, `_multimodal_enricher`)

## Goal

Extract all enrichment-related state and methods into `runtime/enrichment_services.py` as `class EnrichmentServices`, then wire it into `SprintScheduler` via a single `inject_enrichment_services()` setter. No behavioral changes to forensics/multimodal ingestion.

## New File: `runtime/enrichment_services.py`

```
class EnrichmentServices:
    """
    Owns forensics and multimodal enricher lifecycle.

    Lifecycle: init() → enrich_ct_findings() / enrich_findings_multimodal() → flush() → close()

    Fail-safe throughout — all methods are noexcept on None inputs.
    LMDB paths are derived from paths.py (no absolute paths).
    """

    def __init__(
        self,
        forensics_enricher=None,
        forensics_lmdb_env=None,
        multimodal_enricher=None,
        multimodal_lmdb_env=None,
    ):
        self._forensics_enricher = forensics_enricher
        self._forensics_lmdb_env = forensics_lmdb_env
        self._multimodal_enricher = multimodal_enricher
        self._multimodal_lmdb_env = multimodal_lmdb_env

    # ── injection setters ──────────────────────────────────────────────────

    def inject_forensics_enricher(self, enricher, lmdb_env=None): ...
    def inject_multimodal_enricher(self, enricher, lmdb_env=None): ...

    # ── lifecycle (called by SprintScheduler.run()) ───────────────────────

    async def init(self) -> None:
        """F195C: Initialize forensics + multimodal enrichers and LMDBs."""
        await self._init_forensics()
        await self._init_multimodal()

    async def flush(self) -> None:
        """F195C: Flush forensics + multimodal LMDBs (no-op, LMDB auto-flushes)."""
        await self._flush_forensics()
        await self._flush_multimodal()

    async def close(self) -> None:
        """F195C: Close all enrichers and LMDBs at TEARDOWN."""
        await self._close_forensics()
        await self._close_multimodal()

    # ── read sites (called from sprint_ct_log_pipeline) ──────────────────

    async def enrich_ct_findings(self, findings: list) -> None:
        """Enrich CT findings with forensics analysis before storage."""
        ...

    async def enrich_findings_multimodal(self, findings: list) -> None:
        """Enrich PDF/image findings with multimodal analysis before storage."""
        ...

    # ── internal init/close/flush (copied verbatim from SprintScheduler) ──

    async def _init_forensics(self) -> None: ...
    async def _flush_forensics(self) -> None: ...
    async def _close_forensics(self) -> None: ...
    async def _init_multimodal(self) -> None: ...
    async def _flush_multimodal(self) -> None: ...
    async def _close_multimodal(self) -> None: ...
```

### Design Decisions

1. **`__init__` with all-None defaults** — so SprintScheduler can still call `inject_forensics_enricher()` / `inject_multimodal_enricher()` after construction (preserving the current injection pattern). Alternative: two-phase init. Chosen: simple with-None defaults.
2. **All 6 internal methods (`_init_forensics`, `_init_multimodal`, `_flush_forensics`, `_flush_multimodal`, `_close_forensics`, `_close_multimodal`) moved verbatim** — no logic changes, fail-safe guards preserved.
3. **`flush()` is a no-op** — both current implementations are pass; keep for API symmetry.
4. **`enrich_ct_findings` and `enrich_findings_multimodal` rename public methods** — internal names unchanged, public names match the audit's read-site description. No behavior change.
5. **`SprintScheduler` gets single `inject_enrichment_services(services)`** — replaces two injectors with one, but old injectors can remain as passthroughs for backward compatibility (or be removed as dead code after extraction).
6. **LMDB paths via `_get_forensics_lmdb_path()` / `_get_multimodal_lmdb_path()`** — already in sprint_scheduler, imported from `runtime.sprint_scheduler` or `paths`.

## `core/__main__.py` Wiring Changes

1. `from runtime.enrichment_services import EnrichmentServices`
2. After constructing `scheduler` but before `run_sprint()`:
   ```python
   enrichment_services = EnrichmentServices()
   scheduler.inject_enrichment_services(enrichment_services)
   ```
3. Remove the two existing `inject_forensics_enricher()` / `inject_multimodal_enricher()` calls (if any exist in `__main__.py` — grep shows none, so nothing to remove).

Current callers of `inject_forensics_enricher` / `inject_multimodal_enricher` in `__main__.py`:
- None (grep confirms no hits in `core/__main__.py`)

## SprintScheduler Changes

### State fields removed (lines 2022–2026)
```
- self._forensics_enricher: Any = None
- self._forensics_lmdb_env: Any = None
- self._multimodal_enricher: Any = None
- self._multimodal_lmdb_env: Any = None
```

### New field
```python
self._enrichment_services: Optional[EnrichmentServices] = None
```

### New injection method
```python
def inject_enrichment_services(self, services: "EnrichmentServices") -> None:
    self._enrichment_services = services
```

### `run()` init/close sites (lines 2466–2468, 3029–3031) — replace:
```python
# REPLACE:
await self._init_forensics()
await self._init_multimodal()
# WITH:
if self._enrichment_services:
    await self._enrichment_services.init()

# REPLACE:
await self._close_forensics()
await self._close_multimodal()
# WITH:
if self._enrichment_services:
    await self._enrichment_services.close()
```

### `sprint_ct_log_pipeline` enrich sites (lines 7132–7134) — replace:
```python
# REPLACE:
await self._enrich_ct_findings_forensics(findings)
await self._enrich_findings_multimodal(findings)
# WITH:
if self._enrichment_services:
    await self._enrichment_services.enrich_ct_findings(findings)
    await self._enrichment_services.enrich_findings_multimodal(findings)
```

### Old methods removed from SprintScheduler
All `_init_forensics`, `_init_multimodal`, `_flush_forensics`, `_flush_multimodal`, `_close_forensics`, `_close_multimodal`, `_enrich_ct_findings_forensics`, `_enrich_findings_multimodal` — moved to `EnrichmentServices`.

### Old injection methods — keep or deprecate
`inject_forensics_enricher()` and `inject_multimodal_enricher()` remain as no-ops on `SprintScheduler` for backward compatibility (existing tests use them directly), OR are removed if all tests are updated.

## Tests to Update

### Direct state access (tests setting private fields directly)
| File | Lines | Action |
|------|-------|--------|
| `tests/test_forensics_enrichment.py` | 226, 235, 237, 284, 298, 323, 327, 336, 351, 362, 373, 383, 396, 404, 411, 422 | Update to use `EnrichmentServices` or inject via `inject_enrichment_services()` |
| `tests/test_multimodal_analyzer.py` | 306, 315, 317, 363, 377, 402, 406, 415, 430, 441, 452, 462, 475, 490, 501 | Same |
| `tests/test_ct_log_pipeline.py` | 94, 96, 203, 205 | Same |
| `tests/test_forensics_enrichment.py::test_init_forensics_sets_enricher_to_none_on_failure` | 254–265 | Move to `tests/test_enrichment_services.py` |
| `tests/test_forensics_enrichment.py::test_close_forensics_fail_safe_with_none` | 274–286 | Same |
| `tests/test_forensics_enrichment.py::test_close_forensics_close_enricher` | 303–316 | Same |
| `tests/test_multimodal_analyzer.py::test_init_multimodal_sets_enricher_to_none_on_failure` | 334–345 | Same |
| `tests/test_multimodal_analyzer.py::test_close_multimodal_fail_safe_with_none` | 353–365 | Same |
| `tests/test_multimodal_analyzer.py::test_close_multimodal_close_enricher` | 382–395 | Same |

### New test file: `tests/test_enrichment_services.py`
```
TestEnrichmentServices:
  - test_init_forensics_fail_safe (from test_forensics_enrichment::test_init_forensics_sets_enricher_to_none_on_failure)
  - test_init_multimodal_fail_safe (from test_multimodal_analyzer::test_init_multimodal_sets_enricher_to_none_on_failure)
  - test_close_forensics_fail_safe
  - test_close_multimodal_fail_safe
  - test_close_forensics_calls_enricher_close
  - test_close_multimodal_calls_enricher_close
  - test_enrich_ct_findings_empty
  - test_enrich_ct_findings_skips_when_enricher_none
  - test_enrich_ct_findings_skips_when_lmdb_none
  - test_enrich_ct_findings_increments_counter
  - test_enrich_ct_findings_fail_safe
  - test_enrich_findings_multimodal_empty
  - test_enrich_findings_multimodal_skips_when_enricher_none
  - test_enrich_findings_multimodal_skips_when_lmdb_none
  - test_enrich_findings_multimodal_increments_counter
  - test_enrich_findings_multimodal_fail_safe
  - test_inject_forensics_enricher
  - test_inject_multimodal_enricher
```

## Verification After Implementation

```bash
pytest tests/test_enrichment_services.py -q
pytest tests/test_forensics_enrichment.py -q
pytest tests/test_multimodal_analyzer.py -q
pytest tests/test_ct_log_pipeline.py -q
pytest -m unit -q
```

All must pass. No behavior changes to forensics/multimodal ingestion.
# LAYERS_WIRING_REPORT.md

## 1. LayerManager Init

**File:** `layers/layer_manager.py:184`
```python
def __init__(self, config: opt[Dict[str, Any]] = None) -> None:
    self._config = config or {}
    self._layers: Dict[str, Any] = {}
    self._ghost_director: opt[Any] = None
```
Lazy sub-layers via `@property` — no layers loaded until first access.

**init in `core/__main__.py:1270`** — SprintScheduler receives `duckdb_store` as injected param.

**SprintScheduler init:** `_layer_manager` NOT present in sprint_scheduler.py today. LayerManager lives in `legacy/autonomous_orchestrator.py:11422–11438` as `_layer_manager`.

**Required wiring:** Add `_layer_manager: Any = None` to `SprintScheduler.__init__` (~line 2090), init via `_init_layers()` during WARMUP (~line 2550).

---

## 2. Integration Points

### 2a. StealthLayer → fetch pipeline (pre-cycle JA3/UA randomization)

**Hook:** `StealthLayer.rotate_fingerprint()` — `layers/stealth_layer.py:2426`
Returns `BrowserProfile | None`.

**Best call site:** Start of `_run_one_cycle_stable` (~line 6450) / `_run_one_cycle_aggressive` (~line 6732), before `live_public_pipeline.run()`.

**Proposed injection (around line 6450):**
```python
if os.environ.get("HLEDAC_ENABLE_LAYERS") == "1":
    try:
        _lm = get_layer_manager()
        _stealth = getattr(_lm, 'stealth', None)
        if _stealth is not None and hasattr(_stealth, 'rotate_fingerprint'):
            _stealth.rotate_fingerprint()
    except Exception as _e:
        _logger.W("[layers] StealthLayer.rotate_fingerprint failed: %s", _e)
```

### 2b. GhostLayer → post-ingest audit trail

**Actual API:** `GhostLayer.execute_action(action_type: ActionType, parameters: Dict, store_in_vault=True) -> ActionResult`
`layers/ghost_layer.py:242` — **async**, must be awaited.

**IMPORTANT:** `ActionType` enum (`project_types.py:118`) does NOT have a `FINDING_STORED` value. GhostLayer `execute_action()` uses existing ActionType values (SCAN, GOOGLE, DOWNLOAD, SEARCH, etc.) — not arbitrary strings.

**Correct post-ingest hook:** `runtime/telemetry.py:312` — `record_event(phase, event_type, ...)` which is the existing telemetry bus. GhostLayer itself is the GhostDirector wrapper; the audit trail for findings uses `telemetry.record_event("storage", "finding_accepted", {"finding_id": ...})`.

**Ingest call site:** `sprint_scheduler.py:7560`
```python
results = await store.async_ingest_findings_batch(findings)
stored = sum(1 for r in results if isinstance(r, dict) and r.get("accepted"))
```

**Proposed injection (after line 7560):**
```python
if os.environ.get("HLEDAC_ENABLE_LAYERS") == "1" and stored > 0:
    try:
        _lm = get_layer_manager()
        _ghost = getattr(_lm, 'ghost', None)
        if _ghost is not None and stored > 0:
            for r in results:
                if isinstance(r, dict) and r.get("accepted"):
                    finding_id = r.get("finding_id", "?")
                    # GhostLayer.execute_action requires a real ActionType
                    # Use SCAN as the audit action for stored findings
                    from project_types import ActionType
                    await _ghost.execute_action(
                        ActionType.SCAN,  # closest match — findings are scanned/stored
                        {"finding_id": finding_id, "action": "finding_stored"},
                        store_in_vault=True,
                    )
    except StagnationError:
        _logger.critical("[layers] GhostLayer stagnation — re-raising")
        raise
    except Exception as _e:
        _logger.W("[layers] GhostLayer.execute_action failed: %s", _e)
```

**Alternative (better separation):** Use `runtime/telemetry.py:312 record_event()` directly for audit trail instead of `execute_action()` — avoids ActionType constraint entirely.

### 2c. TemporalSignalLayer → post-ingest signal emission

**Actual API:**
- `get_temporal_signal_layer()` — `layers/temporal_signal_runtime.py:134` → singleton
- `TemporalSignalLayer.observe(event: TemporalEvent)` — `temporal_signal_layer.py:171`
- `event_from_finding_like(finding) -> TemporalEvent` — `temporal_signal_layer.py:627`

**Same seam as GhostLayer (line 7560).**

```python
if os.environ.get("HLEDAC_ENABLE_LAYERS") == "1":
    try:
        if is_temporal_store_enabled():
            _tsl = get_temporal_signal_layer()
            for finding in findings:
                _event = event_from_finding_like(finding)
                _tsl.observe(_event)
    except Exception as _e:
        _logger.W("[layers] TemporalSignalLayer.observe failed: %s", _e)
```

### 2d. SecurityLayer → forensic audit gate (post-entropy, pre-canonical-write)

**Actual API:** `MissionAudit.log_action(action_type: str, data: bytes, metadata: Dict) -> str`
`layers/security_layer.py:899`. Access via `SecurityLayer._mission_audit` (line 96).

**Same seam (line 7560).** Finding-scoped, not a gate (no block/reject capability).

```python
if os.environ.get("HLEDAC_ENABLE_LAYERS") == "1":
    try:
        _lm = get_layer_manager()
        _sec = getattr(_lm, 'security', None)
        _audit = getattr(_sec, '_mission_audit', None) if _sec else None
        if _audit is not None:
            for r in results:
                if isinstance(r, dict) and r.get("accepted"):
                    _meta = {
                        "finding_id": str(r.get("finding_id", id(r))),
                        "source": r.get("source_type", "unknown"),
                        "confidence": r.get("confidence", 0.0),
                    }
                    _audit.log_action("finding_accepted", b"", _meta)
    except Exception as _e:
        _logger.W("[layers] SecurityLayer._mission_audit.log_action failed: %s", _e)
```

**SecurityLayer note:** Has NO `scan_finding`/`validate_finding` method. `log_action` is audit-only — cannot block findings. Cannot be wired as quality gate without adding a new method to SecurityLayer.

---

## 3. Fail-Soft Architecture

Every layer hook wrapped identically:
```python
try:
    await _layer.execute_action(...)  # async — must await
except StagnationError:
    _logger.critical("[layers] GhostLayer stagnation — re-raising")
    raise  # NOT swallowed — stagnation = research loop = CRITICAL
except Exception as _e:
    _logger.W("[layers] Layer.op failed: %s", _e)
    # canonical result unaffected — layer is purely advisory
```

**StealthLayer crash mid-sprint:** `rotate_fingerprint()` raises → W logged → fetch proceeds with existing fingerprint profile. Sprint completes normally.

**GhostLayer crash mid-sprint:** `execute_action()` raises → W logged → audit trail has gaps. `StagnationError` is NOT caught and re-raises as CRITICAL.

**TemporalSignalLayer crash mid-sprint:** `observe()` raises → W logged → temporal store misses signal. Advisory hints reduced. Sprint completes normally.

**SecurityLayer crash mid-sprint:** `log_action()` raises → W logged → forensic chain has gap but DuckDB write is unaffected.

---

## 4. HLEDAC_ENABLE_LAYERS Gate

All layer wiring guarded by:
```python
if os.environ.get("HLEDAC_ENABLE_LAYERS") == "1":
```

Default: OFF. Each layer is individually fail-soft via try/except W.

LayerManager is NOT initialized unless the env var is set — avoids any startup cost.

---

## 5. BLOCKED Layers (not wired)

- `neuromorphic_layer` — BLOCKED
- `emergent_communication_layer` — BLOCKED
- `privacy_protection_layer` — BLOCKED
- `coordination_layer` cascade — BLOCKED
- `communication_layer` cascade — BLOCKED
# SECURITY_GATE_COMPLETE.md — Sprint F250E

## validate_finding() Implementation

**File:** `hledac/universal/layers/security_layer.py`

Added `validate_finding(finding: dict) -> tuple[bool, str]` at line ~350 (after `anonymize_text`).

### Logic

| Check | Condition | Action |
|-------|-----------|--------|
| (b) Blocklisted domain | `provenance` host matches `BLOCKLISTED_DOMAINS` | `return (False, "blocklisted_domain")` |
| (a) PII pattern | `payload_text` matches email/phone/SSN regex | `anonymize_text()` → redact in-place, `return (True, "pii_redacted")` |
| (c) Low entropy | Shannon entropy < 1.5 | `return (False, "low_entropy_payload")` |
| Default | — | `return (True, "ok")` |

Fail-soft: any exception → `(True, "ok")` (accept on error).

### Class-level constants

```python
BLOCKLISTED_DOMAINS = frozenset([
    "honeypot.example.com",
    "sinkhole.example.net",
    "known-false-positive.osint.local",
])
ENTROPY_THRESHOLD = 1.5
```

## Wiring — sprint_scheduler.py CT path

**File:** `hledac/universal/runtime/sprint_scheduler.py`

Gate inserted at `~7647` — **before** layer hooks (`GhostLayer`, `TemporalSignalLayer`, `SecurityLayer._mission_audit.log_action`) and **before** `_sidecar_orchestrator.run_target_memory_update`.

```python
# Sprint F250E: Security gate — filter findings before layer hooks and storage
if os.environ.get("HLEDAC_ENABLE_LAYERS") == "1" and accepted_findings:
    security = getattr(self._layer_manager, "security", None)
    if security is not None and hasattr(security, "validate_finding"):
        _sec_accepted: list = []
        _sec_rejected = 0
        _sec_pii_redacted = 0
        for f in accepted_findings:
            _ok, _reason = security.validate_finding(f)
            if not _ok:
                _sec_rejected += 1
                continue   # skip finding
            if _reason == "pii_redacted":
                _sec_pii_redacted += 1
            _sec_accepted.append(f)
        accepted_findings = _sec_accepted
        self._result.security_rejected_count += _sec_rejected
        self._result.pii_redacted_count += _sec_pii_redacted
```

**Fail-soft:** exception in `validate_finding` → finding accepted (don't break pipeline).

## Sprint Scorecard Fields

**File:** `hledac/universal/runtime/sprint_scheduler.py` — `SprintSchedulerResult` (~line 1089)

```python
# Sprint F250E: Security gate telemetry
security_rejected_count: int = 0
pii_redacted_count: int = 0
```

Both exported in `_public_outcome` and `runtime_report` dicts at ~lines 7414 and 10544.

## Export — sprint_exporter.py

**File:** `hledac/universal/export/sprint_exporter.py`

`reject_breakdown` dict now includes:

```python
reject_breakdown = {
    ...
    # Sprint F250E: Security gate
    "security_rejected": scorecard.get("security_rejected_count", 0) or 0,
    "pii_redacted": scorecard.get("pii_redacted_count", 0) or 0,
}
```

Markdown line (assembled at report render time from `reject_breakdown` dict):
```
Security gate: {security_rejected} rejected, {pii_redacted} PII-redacted
```

## Invariants

| # | Invariant | Verification |
|---|-----------|--------------|
| 1 | Gate runs only when `HLEDAC_ENABLE_LAYERS=1` | `os.environ.get("HLEDAC_ENABLE_LAYERS") == "1"` check |
| 2 | Gate runs before sidecar dispatch | Placed before `_dispatch_accepted_findings_sidecars` call chain |
| 3 | Fail-soft: exception in gate does NOT crash sprint | `try/except` wrapping, accepts on error |
| 4 | `validate_finding` is lightweight (<1ms) | Pure Python, no I/O, no async, O(n) text scan |
| 5 | PII redaction redacts in-place, does NOT reject | `finding["payload_text"] = redacted`, returns `True/"pii_redacted"` |
| 6 | Blocklisted domain → reject (not redact) | Returns `(False, "blocklisted_domain")` |
| 7 | Entropy check is last, after PII (PII text may have low entropy) | Order: blocklist → PII → entropy |
| 8 | Counters are accumulated across all CT cycles | `+= _sec_rejected`, `+= _sec_pii_redacted` |
| 9 | Exporter else-branch (no dedup_status) preserves security counters | Fixed: `reject_breakdown` dict always has `security_rejected`/`pii_redacted` keys |

## Files Modified

| File | Change |
|------|--------|
| `layers/security_layer.py` | +65 lines: `BLOCKLISTED_DOMAINS`, `ENTROPY_THRESHOLD`, `validate_finding()`, `_shannon_entropy()` |
| `runtime/sprint_scheduler.py` | +35 lines: scorecard fields, gate loop, outcome exports |
| `export/sprint_exporter.py` | +2 lines: `reject_breakdown` keys |

## Verification

All symbols confirmed present via grep:
- `validate_finding` ✓
- `BLOCKLISTED_DOMAINS` ✓
- `ENTROPY_THRESHOLD` + `_shannon_entropy` ✓
- `security_rejected_count` + `pii_redacted_count` in scheduler ✓
- `security_rejected` + `pii_redacted` in exporter ✓
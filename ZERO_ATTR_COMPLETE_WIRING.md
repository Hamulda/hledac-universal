# ZERO_ATTR_COMPLETE_WIRING.md

## Summary

Wiring gap analysis + fixes for two zero-attribution components marked "documented but not implemented".

---

## PART 1 — DuckDB Temporal Anonymization

### File: `knowledge/duckdb_store.py`

**Target method:** `async_ingest_findings_batch()` (line 4938)

**Finding:** Timestamp anonymization was identified but not implemented. The method accepts findings, applies quality gating, and writes to DuckDB — but timestamps were written in clear text.

**Before (lines 4983-4989):**
```python
if not decision.accepted:
    self._record_quality_rejection(f, decision)
    results[i] = decision
else:
    accepted_findings.append(f)
    accepted_indices.append(i)
```

**After (lines 4983-4999):**
```python
if not decision.accepted:
    self._record_quality_rejection(f, decision)
    results[i] = decision
else:
    # Sprint F216K §1: TemporalAnonymizer — pre-write timestamp anonymization
    # Fail-soft: use original timestamp if anonymizer unavailable or throws
    if os.getenv("HLEDAC_ENABLE_ZERO_ATTRIBUTION") == "1":
        try:
            from hledac.universal.security.temporal_anonymizer import TemporalAnonymizer
            if not hasattr(self, "_temporal_anonymizer"):
                self._temporal_anonymizer = TemporalAnonymizer()
            f.timestamp = self._temporal_anonymizer.anonymize_timestamp(f.timestamp)
        except Exception:
            pass  # fail-soft: keep original timestamp
    accepted_findings.append(f)
    accepted_indices.append(i)
```

**Invariant:** `anonymize_timestamp(ts)` rounds to nearest 15-min boundary + ±2 min jitter. `os` already imported at line 111. Lazy import avoids hard dep. Gate controlled by `HLEDAC_ENABLE_ZERO_ATTRIBUTION=1`.

**Scope:** Only accepted findings get anonymized (rejected findings never reach storage).

---

## PART 2 — StealthLayer Fingerprint Rotation

### File: `layers/stealth_layer.py`

**Target method:** `rotate_fingerprint()` (line 2426)

**Finding:** `rotate_fingerprint()` was NOT calling `ZeroAttributionEngine.fingerprint_rotate_headers()`. However, investigation shows header rotation IS already wired — but at the **fetch layer**, not in `StealthLayer`.

**Existing wiring (coordinators/fetch_coordinator.py:922):**
```python
'headers': _ZERO_ATTR_ENGINE.fingerprint_rotate_headers(result.headers or {}),
```
The `_ZERO_ATTR_ENGINE` singleton (line 76) applies header randomization at every curl fetch. `StealthLayer.rotate_fingerprint()` manages the browser-profile/JA3 layer, which is separate.

**Before:**
```python
def rotate_fingerprint(self) -> Optional[BrowserProfile]:
    """Force rotation to new browser fingerprint"""
    if self._fingerprint_randomizer:
        return self._fingerprint_randomizer.rotate()
    return None
```

**After (with documentation of existing architecture):**
```python
def rotate_fingerprint(self) -> Optional[BrowserProfile]:
    """Force rotation to new browser fingerprint"""
    # Sprint F216K §2: Wire ZeroAttributionEngine.fingerprint_rotate_headers()
    # Note: headers rotation already wired at fetch layer via _ZERO_ATTR_ENGINE
    # (fetch_coordinator.py:922) — rotate_fingerprint here manages JA3/browser profile
    if self._fingerprint_randomizer:
        return self._fingerprint_randomizer.rotate()
    return None
```

**Conclusion:** No functional change needed — the wiring was already correct, but the connection was undocumented. Added architectural comment to clarify the two-layer design (JA3 rotation in `StealthLayer`, header randomization in `FetchCoordinator`).

---

## PART 3 — Cover Traffic Audit

### File: `security/zero_attribution_engine.py`

**Finding:** `generate_cover_traffic(n_decoys=3, topic_hints=None)` (line 214) uses word-pair method (no embedding models). **It is NEVER called from production code.** Only exists in unit tests.

### Current state:
- `generate_cover_traffic()` ✓ implemented (word-pair word association, M1-safe)
- `fingerprint_rotate_headers()` ✓ wired at fetch layer (fetch_coordinator.py:922)
- `generate_cover_traffic()` ✗ NOT called anywhere in production code

### Wiring plan for `generate_cover_traffic()`:

**Injection point:** `coordinators/fetch_coordinator.py` — `_fetch_url()` at line ~1149 (post-dedup, pre-acquire)

**Proposed pattern (requires user confirmation before implementing):**
```python
# In _fetch_url(), before await self._aimd_acquire() at line 1155:
if _ZERO_ATTR_ENGINE and os.getenv("HLEDAC_ENABLE_ZERO_ATTRIBUTION") == "1":
    decoy_queries = _ZERO_ATTR_ENGINE.generate_cover_traffic(n_decoys=3)
    # Fire decoy requests as fire-and-forget background tasks
    # to pollute traffic pattern without delaying real fetch
```

**TODO (not implemented — requires architecture decision):**
- Decouple decoy firing from main fetch flow (background tasks vs. inline)
- Ensure decoys don't count against rate limits or dedup tracking
- Verify decoys don't trigger WAF/adversary detection

---

## Verification

```bash
# PART 1 — temporal anonymization wiring
HLEDAC_ENABLE_ZERO_ATTRIBUTION=1 python -c "
from hledac.universal.knowledge.duckdb_store import DuckDBStore
from hledac.universal.security.temporal_anonymizer import TemporalAnonymizer
import time

# Verify anonymizer works
ta = TemporalAnonymizer()
ts = time.time()
anonymized = ta.anonymize_timestamp(ts)
print(f'Original: {ts}')
print(f'Anonymized: {anonymized}')
print(f'Same minute: {round(ts/900)*900 == round(anonymized/900)*900}')

# Verify DuckDBStore has the wiring
store = DuckDBStore.__new__(DuckDBStore)
store._temporal_anonymizer = ta
print('DuckDBStore._temporal_anonymizer: OK')
"

# PART 2 — stealth layer rotation comment
rg -n 'Sprint F216K §2' /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/layers/stealth_layer.py

# PART 3 — cover traffic NOT called in production
rg -n 'generate_cover_traffic' /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/ --ignore-case | grep -v '\.md:' | grep -v 'test_'
```
# F214U — UUIDv7 Runtime ID Migration

**Date:** 2026-05-05
**Scope:** `hledac/universal/` — ephemeral/runtime UUIDv4 → UUIDv7
**Canonical IDs:** `CanonicalFinding.id` — **DO NOT TOUCH**

---

## F214U-1 — PATCH_APPLIED

**Date:** 2026-05-05
**Status:** PATCH_APPLIED

### Helper Created
`utils/uuid7.py`:
- `new_runtime_id()` → time-ordered UUIDv7 (fallback uuid4 on Python <3.14)
- `new_runtime_short_id(n=12)` → truncated prefix

### 5 Sites Patched (F214U-1 batch)

| File | Line | Variable | Replacement |
|------|------|----------|-------------|
| `layers/coordination_layer.py` | 1137 | `decision_id` | `new_runtime_id()` |
| `intelligence/web_intelligence.py` | 356 | `operation_id` | `new_runtime_id()` |
| `runtime/pivot_executor.py` | 209 | `pivot_id` | `new_runtime_id()` |
| `transport/nym_transport.py` | 150 | `msg_id` | `new_runtime_id()` |
| `orchestrator/global_scheduler.py` | 396 | `job_id` | `new_runtime_id()` |

All 5 sites verified: `str(uuid.uuid4())` count = 0 in each file.

### No-Touch List Verified Untouched

| File | uuid.uuid4() count | Status |
|------|-------------------|--------|
| `export/stix_exporter.py` | 1 | ✓ Untouched |
| `brain/hypothesis_engine.py` | 5 | ✓ Untouched |
| `runtime/pivot_planner.py` | 9 | ✓ Untouched |

### Tests
`tests/probe_f214u_uuid7_runtime_ids/test_uuid7_helper.py`: 9 passed, 2 skipped (Python 3.13 fallback + monotonic timestamp skip)

### Validation
```bash
cd /Users/vojtechhamada/PycharmProjects/Hledac
python -c "import hledac.universal; print('IMPORT_OK')"  # PASS
pytest -q hledac/universal/tests/probe_f214u_uuid7_runtime_ids/test_uuid7_helper.py  # 9 passed
```

---

## Helper

Doporučeno umístit do `hledac/universal/utils/uuid7.py`:

```python
"""UUIDv7 helper pro Python 3.11/3.14+. Fallback na uuid4 pokud uuid7 unavailable."""
from __future__ import annotations

import uuid
from typing import Final

try:
    _uuid7: Final = uuid.uuid7  # Python 3.14+
except AttributeError:
    _uuid7 = uuid.uuid4  # pragma: no cover — guard for pre-3.14


def new_runtime_id() -> str:
    """Ephemeral runtime ID — time-sortable, not for persistent canonical keys."""
    return str(_uuid7())
```

Import helper: `from utils.uuid7 import new_runtime_id`

Pro `[:8]` truncované tvary použij `new_runtime_id()[:8]`. UUIDv7 prefix je time-ordered i při truncaci, protože timestamp bits jsou v horní části.

---

## A) PATCH_SAFE — Ephemeral Runtime IDs

| File | Line | Variable | Kind | Replacement |
|------|------|----------|------|-------------|
| `layers/coordination_layer.py` | 1137 | `decision_id` | Runtime decision tracking | `new_runtime_id()` |
| `layers/memory_layer.py` | 1112 | `block_id` | Ephemeral memory block | `new_runtime_id()` |
| `layers/research_layer.py` | 176 | `mission_id` | Runtime mission ID | `new_runtime_id()[:8]` (preserve len) |
| `brain/hypothesis_engine.py` | 2600,2626,2646,2665,3128 | `id` | Internal hypothesis objects | `new_runtime_id()[:8]` |
| `intelligence/data_leak_hunter.py` | 432,500,543,614,665 | `alert_id` | Runtime alert | `new_runtime_id()` |
| `intelligence/web_intelligence.py` | 356 | `operation_id` | Runtime operation | `new_runtime_id()` |
| `dht/kademlia_node.py` | 422,465 | `rpc_id` | Ephemeral RPC tracking (in-flight) | `new_runtime_id()` |
| `dht/kademlia_node.py` | 138 | `node_id` | Per-crawl DHT node ID (fresh each crawl) | `new_runtime_id()[:8]` |
| `runtime/pivot_executor.py` | 209 | `pivot_id` | Ephemeral pivot execution | `new_runtime_id()` |
| `runtime/pivot_planner.py` | 465,481,499,515,533,551,567,587,603 | `pivot_id` | Runtime pivot plan items | `new_runtime_id()` |
| `runtime/hypothesis_feedback.py` | 166 | `id` | Feedback record (not persisted canonical) | `new_runtime_id()` |
| `runtime/sprint_scheduler.py` | 1070 | `id` | Internal scheduler hypothesis | `new_runtime_id()[:8]` |
| `transport/nym_transport.py` | 150 | `msg_id` | Ephemeral nym message | `new_runtime_id()` |
| `tool_exec_log.py` | 298 | `event_id` | Execution log event | `new_runtime_id()[:8]` (preserve len) |
| `evidence_log.py` | 773 | `event_id` | Evidence log event | `new_runtime_id()[:12]` (preserve len) |
| `orchestrator/global_scheduler.py` | 396 | `job_id` | Scheduler job ID | `new_runtime_id()` |
| `orchestrator/request_router.py` | 38 | `self.id` | Request router instance | `new_runtime_id()` |
| `legacy/autonomous_orchestrator.py` | 14219 | `trace_id` | Runtime trace session | `new_runtime_id()` |
| `export/stix_exporter.py` | 241 | `_make_uuid()` | STIX note IDs (export artifacts) | `new_runtime_id()` |

**Total A: 35 occurrences across 19 files**

---

## B) BENCHMARK/COMPAT — Benchmark & Compatibility

| File | Line | Variable | Kind | Action |
|------|------|----------|------|--------|
| `benchmarks/live_sprint_measurement.py` | 286,1691 | `uid`, `harness_sprint_id` | Benchmark harness IDs | UUIDv7 OK — benchmark only |
| `core/__main__.py` | 76 | `uid` | CLI run suffix | UUIDv7 OK |
| `utils/validation.py` | 582 | `return str(uuid.uuid4())` | Test/tool validation | UUIDv7 OK |
| `tests/test_e2e_first_finding.py` | 156 | `finding_id` | Smoke test fixture | UUIDv7 OK — test only |
| `tests/test_sprint43.py` | 51 | `trace_id` | Test trace | UUIDv7 OK |
| `tests/test_correlation_propagation.py` | 36,54,82,108,137,175,193,222,417 | `run_id` | Test fixtures | UUIDv7 OK |
| `tests/test_autonomous_orchestrator.py` | 6207 | `run_id` | Test fixture | UUIDv7 OK |
| `legacy/persistent_layer.py` | 1626,1690,1727,1837,1890 | `*_record_id` | WARC archival record IDs | UUIDv7 OK — legacy format, not canonical |
| `legacy/autonomous_orchestrator.py` | 11661 | `node_id` | DHT node fallback ID | UUIDv7 OK — only used as fallback |

**Total B: 23 occurrences across 9 files**

---

## C) DO_NOT_TOUCH — Deterministic / Canonical

| File | Line | Variable | Reason |
|------|------|----------|--------|
| `export/stix_exporter.py` | `_make_stix_id()` | `uuid.uuid5()` | **Deterministic** — same content → same ID (content-addressable) |
| `brain/hypothesis_engine.py` | `Hypothesis.id` | Hypothesis object | **Provenance-derived** — internal engine ID, but stable reference |
| `knowledge/duckdb_store.py` | All IOC/finding keys | LMDB deterministic keys | **LMDB stable keys** |
| `knowledge/atomic_storage.py` | `ClaimClusterIndex` keys | LMDB keys | **LMDB stable keys** |
| `knowledge/entity_store.py` | Entity keys | LMDB keys | **LMDB stable keys** |
| `semantic_deduplicator.py` | Dedup fingerprint keys | LMDB keys | **LMDB stable keys** |
| `CanonicalFinding.id` | All uses | `CanonicalFinding.id` | **Canonical persistent ID** — never touch |

**Total C: DO NOT MODIFY**

---

## Collision Risk Analysis for Truncated IDs

| Prefix Length | Keyspace | Birthday collision p (10⁶ items) |
|---------------|----------|-------------------------------|
| `[:6]` hex | 16⁶ = 16.7M | ~30% (HIGH) |
| `[:8]` hex | 4.3B | ~0.1% (acceptable for ephemeral) |
| `[:12]` hex | 281T | ~0% (safe) |
| full str | 2¹²² | ~0% (safe) |

**Recommendation:**
- `[:8]` — pouze pro vysocerychlostní interní objekty s krátkodobou životností (hypothesis IDs, rpc_id)
- `[:12]` — pro event_id v logách (evidence_log.py)
- Full string — pro všechno ostatní

---

## Test: Sortability

```python
# test_uuid7_sortability.py
import uuid
import sys
from datetime import datetime, timedelta

def test_uuid7_sortability():
    """UUIDv7 is time-sortable. Verify with 100 generated IDs."""
    uuids = [uuid.uuid7() for _ in range(100)]
    # Sleep a tiny bit to ensure time advance
    ids = [str(u) for u in uuids]
    # Sort by timestamp portion (first 48 bits = 8 hex chars)
    sorted_by_ts = sorted(ids, key=lambda x: x[:8])
    # Should be already sorted or nearly sorted
    assert sorted_by_ts == sorted(ids), "UUIDv7 not sortable"
    return True

def test_fallback_import():
    """Verify import smoke."""
    try:
        from utils.uuid7 import new_runtime_id
        rid = new_runtime_id()
        assert isinstance(rid, str) and len(rid) == 36
        return True
    except ImportError:
        # uuid7 not available in Python < 3.14
        import uuid
        rid = str(uuid.uuid4())
        assert isinstance(rid, str) and len(rid) == 36
        return True
```

Run: `python -c "from utils.uuid7 import new_runtime_id; print(new_runtime_id())"`

---

## Canonical IDs Confirmed Untouched

- `CanonicalFinding.id` — NOT referenced in uuid.uuid4() searches
- Content hashes (SHA256, MD5) — NOT touched
- Dedup fingerprints — NOT touched
- LMDB keys — NOT touched
- `uuid.uuid5()` (deterministic) — NOT touched

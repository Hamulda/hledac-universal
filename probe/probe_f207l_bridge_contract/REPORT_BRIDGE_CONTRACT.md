# SPRINT F207L-C — Source Finding Bridge Contract Seal

**Date:** 2026-05-03
**Status:** COMPLETE

---

## Objective

Ensure `runtime/source_finding_bridge.py` is the **only authoritative bridge module** for non-feed finding conversion (CT, Wayback, PassiveDNS).

---

## Verification Results

### 1. runtime/source_finding_bridge.py Contract ✅

| Requirement | Status |
|-------------|--------|
| `ct_results_to_findings` | ✅ Present |
| `wayback_results_to_findings` | ✅ Present |
| `passive_dns_results_to_findings` | ✅ Present |
| `MAX_BRIDGE_OUTPUT = 500` | ✅ Present |
| BLAKE2b-128 deterministic IDs | ✅ Present (`_make_blake2b_hex`, 16-byte digest) |
| Rejection: `missing_domain` | ✅ `REJECTION_MISSING_DOMAIN` |
| Rejection: `missing_value` | ✅ `REJECTION_MISSING_VALUE` |
| Rejection: `low_information` | ✅ `REJECTION_LOW_INFORMATION` |
| Rejection: `duplicate_candidate` | ✅ `REJECTION_DUPLICATE_CANDIDATE` |
| Rejection: `unsupported_shape` | ✅ `REJECTION_UNSUPPORTED_SHAPE` |

### 2. No Runtime Import from probe_f207j_nonfeed_finding_bridge ✅

`runtime/source_finding_bridge.py` docstring references the test path (for verification instructions), but **no runtime module imports from `probe_f207j_nonfeed_finding_bridge`**.

```bash
$ rg "probe_f207j_nonfeed_finding_bridge" runtime/
# (only the docstring comment — no actual import)
```

### 3. Boundary Constraints ✅

| Constraint | Verified |
|-------------|----------|
| No DB write | ✅ AST inspection: zero `execute`/`commit`/`upsert` calls |
| No graph write | ✅ AST inspection: zero `graph_service`/`upsert_ioc` calls |
| No live network | ✅ Tests use mocks only, no socket imports |
| No scheduler import | ✅ `sprint_scheduler` not in module namespace |
| Deterministic IDs | ✅ BLAKE2b-64 (16-byte) hex, no `hash()` builtin |

### 4. Test Results ✅

```bash
$ python -m pytest tests/probe_f207l_bridge_contract/ -v
34 passed in 5.17s
```

All tests import from `runtime.source_finding_bridge` (not the probe module).

### 5. Existing Tests (probe_f207j/probe_f207k)

`tests/probe_f207j_nonfeed_finding_bridge/` and `tests/probe_f207k_nonfeed_accepted_path/` still import from the probe module. These are **read-only** per sprint contract — not edited. The probe module remains as legacy reference but is not used by any runtime code.

---

## Files Created

| File | Purpose |
|------|---------|
| `tests/probe_f207l_bridge_contract/__init__.py` | Package marker |
| `tests/probe_f207l_bridge_contract/test_bridge_contract.py` | 34 tests, all from runtime module |
| `probe_f207l_bridge_contract/REPORT_BRIDGE_CONTRACT.md` | This report |
| `probe_f207l_bridge_contract/bridge_contract.json` | Machine-readable contract state |

---

## Key Findings

1. **runtime/source_finding_bridge.py is authoritative and self-contained** — all conversion logic, rejection reasons, and bounds defined in one module.

2. **No runtime dependency on probe bridge** — verified no `probe_f207j_nonfeed_finding_bridge` imports exist in `runtime/`.

3. **Contract stable** — the two bridge modules (`probe_f207j` and `runtime`) are functionally equivalent; the runtime version is the one used by scheduler (F207K-A integration).

---

## Abort Conditions Check

| Condition | Status |
|-----------|--------|
| Scheduler edit | ❌ Not touched |
| Acquisition strategy edit | ❌ Not touched |
| Adapter edit | ❌ Not touched |
| Live network | ❌ Not used |
| DB/graph write | ❌ Not used |

**All abort conditions avoided.**
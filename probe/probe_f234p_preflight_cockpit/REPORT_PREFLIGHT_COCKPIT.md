# Pre-Live Artifact Cockpit Report

**Verdict:** ✅ `READY_FOR_FEED_BASELINE_ONLY`
**Live Allowed:** True
**Next Action:** 🚀 `run_nonfeed_diagnostic`
**Next Action Detail:** feed baseline ready; nonfeed capability blocked

## Feed Baseline vs Capability Readiness (F234P)

| Capability Axis | Status |
|-----------------|--------|
| **Feed Baseline Allowed** | ✅ YES |
| **Nonfeed Capability Live Allowed** | ❌ NO |

**Capability Blockers:**
  - `F224 blocking artifacts missing`
  - `F231 evidence lift pack missing`
**Next Action (Feed Baseline):** `python -m core --profile nonfeed_diagnostic --query "mozilla.org certificate transparency subdomains" --live --require-memory-ok`
**Next Action (Capability):** `run missing probe lanes to restore capability`

## Decision Gate

- **Gate Decision:** `READY_FOR_FEED_BASELINE_ONLY`
- **Gate Live Allowed:** True

## Artifact Pack

| Status | Count |
|--------|-------|
| Total  | 8 |
| Ready  | 5 |
| Missing | 3 |
| Stale   | 0 |

**Missing Required Probes:**
  - `probe_f224a_worker_pool_import_seal`
  - `probe_f224c_discovery_provider_gap`
  - `probe_f231a_public_candidate_ledger`

## Memory (UMA)

- **System Used:** 1.20 GiB
- **Swap Used:** 0.30 GiB
- **Swap Detected:** True
- **UMA State:** `ok`
- **IO Only:** False
- **Hardware Constrained:** `False`
- **Swap Policy Tier:** `unknown`
- **Swap Gate Reason:** ``

## Provider Surface

- **OK:** True

## Next Actions

1. `run_nonfeed_diagnostic`

F220-like feed-only detected. Run nonfeed diagnostic profile:
```bash
feed baseline ready; nonfeed capability blocked
```

---
*Profile: `nonfeed_diagnostic` | Query: `mozilla.org certificate transparency subdomains april 2026`*
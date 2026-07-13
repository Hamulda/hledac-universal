# R0: Nonfeed Reality Lock Audit Report

**Generated:** 2026-07-13T13:12:26+0200  
**Total checks:** 13  
**Passed:** 12  
**Failed:** 1  
**Verdict:** `R0_LOCK_FAIL`

## R0 Invariants

| Q | Check | Result | Detail |
|---|-------|--------|--------|
| Q1 | `canonical_sprint_owner` | ✅ PASS | CANONICAL_SPRINT_OWNER='hledac.universal.core.__main__.run_sprint' expected='hledac.universal.core.__main__.run_sprint' |
| Q2-Q3 | `scheduler_imports_acquisition_strategy` | ✅ PASS | imported=['hledac.universal.runtime.acquisition_strategy'] |
| Q2-Q3 | `scheduler_calls_run_enabled_acquisition_lanes` | ❌ FAIL | present=False |
| Q2-Q3 | `scheduler_imports_source_finding_bridge` | ✅ PASS | imported=['hledac.universal.runtime.source_finding_bridge'] |
| Q4 | `crtsh_adapter_call_crtsh_callable` | ✅ PASS | from discovery.crtsh_adapter import call_crtsh |
| Q4 | `passive_dns_call_lookup_callable` | ✅ PASS | from security.passive_dns import call_lookup_passive_dns |
| Q4 | `wayback_diff_miner_class_exists` | ✅ PASS | from intelligence.wayback_diff_miner import WaybackDiffMiner |
| Q5 | `source_finding_bridge.ct_results_to_findings_callable` | ✅ PASS | family=CT |
| Q5 | `source_finding_bridge.wayback_results_to_findings_callable` | ✅ PASS | family=Wayback |
| Q5 | `source_finding_bridge.passive_dns_results_to_findings_callable` | ✅ PASS | family=PassiveDNS |
| Q9 | `ledger_family_constants` | ✅ PASS | PUBLIC/CT/WAYBACK/PASSIVE_DNS/PIVOT defined |
| Q9 | `ledger_stage_constants` | ✅ PASS | 6 stages defined |
| Q9 | `ledger_max_size_bound` | ✅ PASS | MAX_LEDGER_SIZE=500 |

## Probe Methodology

Hermetický read-only audit, který:
1. Parsuje `runtime/sprint_scheduler.py` AST a hledá importy + volání
2. Importuje `runtime_authority_manifest` a ověřuje canonical owner
3. Importuje discovery/security/intelligence adaptéry (read-only)
4. Ověřuje `runtime.source_finding_bridge` konvertory
5. Importuje `runtime.nonfeed_candidate_ledger` a validuje konstanty

## Re-run

```bash
PYTHONPATH=. python tools/probe_r0_nonfeed_reality_lock.py
```

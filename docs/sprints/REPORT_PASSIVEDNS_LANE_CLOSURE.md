# Sprint R2: PassiveDNS Lane Closure — Report

## Goal

Close PassiveDNS raw → bridge → store → outcome path WITHOUT creating a new runner file.
Use existing `call_lookup_passive_dns`, `PassiveDNSOutcome`, and `passive_dns_results_to_findings`.
Use existing DuckDBShadowStore. Re-raise `asyncio.CancelledError`. Enforce plan max_items and timeout.

## Outcome Contract

`AcquisitionLaneOutcome` for PASSIVE_DNS lane:

| Field | Type | Description |
|-------|------|-------------|
| `lane` | `AcquisitionLane.PASSIVE_DNS` | Lane identifier |
| `enabled` | `bool` | Whether lane is enabled |
| `attempted` | `bool` | True if lookup was attempted |
| `timeout` | `bool` | True if timeout occurred |
| `error` | `str \| None` | Error message or skip reason |
| `source_family` | `"passive_dns"` | Source family identifier |
| `passive_dns_raw_count` | `int` | Raw IP count from PDNS lookup |
| `candidate_findings` | `tuple` | CanonicalFinding candidates |
| `rejection_reasons` | `tuple` | Bridge rejection reasons |
| `rejected_count` | `int` | Number of bridge rejections |
| `sample_rejections` | `tuple` | Sample of rejection reasons |
| `accepted_findings` | `int` | Count accepted by store |

## Path Implemented

```
AcquisitionStrategySnapshot (PASSIVE_DNS plan)
  → run_enabled_acquisition_lanes()
    → _run_pdns_lane(plan)          [existing: acquisition_strategy.py:2559]
      ├── asyncio.timeout(plan.timeout_s)
      ├── build_lane_query(query, AcquisitionLane.PASSIVE_DNS)
      ├── call_lookup_passive_dns(shaped_query)  → (ips, PassiveDNSOutcome)
      ├── passive_dns_results_to_findings(ips, outcome, query, sprint_id)
      ├── store.async_ingest_findings_batch(candidates)
      ├── [I6] except asyncio.CancelledError: raise
      └── return AcquisitionLaneOutcome(...)
```

## Key Invariant: [I6] CancelledError Propagation

```python
except asyncio.CancelledError:
    raise  # [I6] propagate CancelledError
```

Placed AFTER `asyncio.TimeoutError` handler and BEFORE generic `Exception` handler.
This ensures CancelledError from external cancellation is NOT swallowed.

## Canonical Storage Path

Candidates passed to `store.async_ingest_findings_batch(candidates)` for canonical DuckDB write.

## Ledger Integration

`NonfeedCandidateLedger` receives events with `FAMILY_PASSIVE_DNS` for STAGE_STORED, STAGE_REJECTED, STAGE_PROVIDER_FAILED stages.

## Tests: 27 Passing

| Test | Description |
|------|-------------|
| `test_outcome_has_required_fields` | Outcome has all required fields |
| `test_empty_result_outcome` | Empty result handled correctly |
| `test_timeout_outcome` | Timeout terminal state set |
| `test_error_outcome` | Error terminal state set |
| `test_skip_outcome` | Skip outcome when disabled |
| `test_ip_list_to_candidates` | IP list converts to CanonicalFinding |
| `test_empty_ip_list_rejects` | Empty IP list rejected by bridge |
| `test_private_ip_rejected` | Private IPs filtered by bridge |
| `test_duplicate_pair_rejected` | Duplicate candidates rejected |
| `test_ledger_receives_passive_dns_stored_event` | Ledger receives stored event |
| `test_ledger_receives_passive_dns_rejected_event` | Ledger receives rejected event |
| `test_ledger_receives_passive_dns_provider_failed_event` | Ledger receives provider failed |
| `test_accumulate_pdns_accepted_findings` | Scheduler accumulates accepted |
| `test_accumulate_pdns_rejection_reflected` | Rejection reflected in outcome |
| `test_ingest_called_with_candidates` | Store receives candidates |
| `test_ingest_partial_accepted` | Partial acceptance handled |
| `test_domain_query_attempts_passive_dns` | Domain query triggers PDNS |
| `test_ip_query_attempts_passive_dns` | IP query triggers PDNS |
| `test_empty_provider_result_empty_terminal_state` | Empty provider result state |
| `test_timeout_outcome_records_timeout` | Timeout state recorded |
| `test_adapter_exception_records_error` | Adapter error recorded |
| `test_cancelled_error_propagates` | CancelledError propagates [I6] |
| `test_candidates_built_from_ip_list` | IP list builds candidates |
| `test_storage_accepted_reflected_in_accepted_count` | Storage accepted counted |
| `test_storage_rejection_reflected_in_rejected_count` | Storage rejection counted |
| `test_no_network_imports` | No live network imports |
| `test_pdns_outcome_schema_complete` | PassiveDNSOutcome schema complete |

## Regression: CT Lane Closure

```
tests/ct_lane_closure/: 20 passed
```

No regressions introduced.

## Files

- Modified: `runtime/acquisition_strategy.py` (added [I6] CancelledError re-raise)
- Created: `tests/probe_r2_passivedns_lane_closure/test_passivedns_lane_closure.py`

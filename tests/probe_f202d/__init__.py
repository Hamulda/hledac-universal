"""
Sprint F202D: Leak and Secret Sentinel — Probe Tests
====================================================

Invariant mapping:
  F202D-1 | source_type is "leak_sentinel" for breach findings
  F202D-2 | payload_text contains evidence envelope (JSON with audit_reason, evidence_pointers, signal_facets, suggested_pivots)
  F202D-3 | No raw secrets in findings — all secrets masked via pii_gate
  F202D-4 | Bounded: MAX_TOTAL_FINDINGS=100 cap applied
  F202D-5 | All findings go through async_ingest_findings_batch
  F202D-6 | _run_leak_sentinel_sidecar is called after CT findings are accepted
  F202D-7 | SprintSchedulerResult.leak_findings_produced is set
  F202D-8 | Fail-soft: sidecar errors do not crash sprint
  F202D-9 | Source types: paste_leak, github_secret, leak_sentinel
  F202D-10 | Masked secrets use last-4-chars preservation pattern
"""

import json

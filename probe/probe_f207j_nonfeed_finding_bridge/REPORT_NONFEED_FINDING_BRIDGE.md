# REPORT: Non-Feed Adapter Finding Bridge — Sprint F207J-B

## Sprint Metadata

| Field | Value |
|-------|-------|
| Sprint | F207J-B |
| Date | 2026-05-03 |
| Owner | Vojtech Hamada |
| Files Created | 4 |
| Test Count | 32 passed |

---

## Adapter → Finding Bridge Mapping

### CT (crt.sh / discovery/crtsh_adapter.py)

**Helper**: `ct_results_to_findings(batch_result, outcome, query, sprint_id)`

| Output Field | Source |
|---|---|
| `source_type` | `"ct"` |
| `confidence` | `0.6` |
| `finding_id` | `ct-{blake2b(domain)[:16]}-{sprint_id[:8]}` |
| `provenance[0]` | `ct:{domain}` |
| `provenance[1]` | `query:{query}` |
| `provenance[2]` | `sprint:{sprint_id[:16]}` |
| `payload_text` | `hit.snippet` (truncated to 2000 chars) |

**Rejection Reasons**:
- `missing_domain` — URL and title yield no domain string
- `low_information` — domain in private set OR no TLD dot
- `duplicate_candidate` — same domain already in batch (blake2b dedup)

---

### Wayback Diff (intelligence/wayback_diff_miner.py)

**Helper**: `wayback_results_to_findings(diff_result, query, sprint_id)`

| Output Field | Source |
|---|---|
| `source_type` | `"wayback_diff"` |
| `confidence` | `0.75` |
| `finding_id` | `wdiff-{blake2b(digest)[:16]}-{timestamp[:8]}` |
| `provenance[0]` | `wayback:{url}` |
| `provenance[1]` | `digest:{digest}` |
| `provenance[2]` | `change:{change_type}` |
| `provenance[3]` | `ts:{timestamp}` |
| `payload_text` | Multi-line: change_type, url, digest, timestamp, evidence_url |

**Rejection Reasons**:
- `missing_value` — event has no digest
- `low_information` — `change_type == "unchanged"` (no signal)
- `unsupported_shape` — not a WaybackDiffResult

---

### PassiveDNS (security/passive_dns.py)

**Helper**: `passive_dns_results_to_findings(ips, outcome, query, sprint_id)`

| Output Field | Source |
|---|---|
| `source_type` | `"passive_dns"` |
| `confidence` | `0.5` |
| `finding_id` | `pdns-{blake2b(query:ip)[:16]}-{sprint_id[:8]}` |
| `provenance[0]` | `domain:{query}` |
| `provenance[1]` | `ip:{ip}` |
| `provenance[2]` | `sprint:{sprint_id[:16]}` |
| `provenance[3]` | `source:circl_pdns` |
| `payload_text` | `domain: {query}\nip: {ip}\nsource: CIRCL PDNS` |

**Rejection Reasons**:
- `missing_domain` — query is empty/whitespace
- `missing_value` — IP list is empty
- `low_information` — IP is private/reserved (10.x, 192.168.x, 127.x, ::1, fe80:, etc.)
- `duplicate_candidate` — same (domain, ip) pair already in batch

---

## Key Design Decisions

### 1. No hash() builtin
All finding IDs use `hashlib.blake2b` with a per-type salt for determinism.
```
_make_blake2b_hex(value, salt) → first 16 hex chars of BLAKE2b-128
```

### 2. blake2b over blake2s
`blake2b(digest_size=16)` is used — safe on all platforms, faster than SHA-256.

### 3. Rejection reason semantics
- `missing_domain` — extraction returned None (nothing found)
- `missing_value` — required signal value (digest, IPs) absent
- `low_information` — value found but structurally uninformative
- `duplicate_candidate` — same signal already emitted in this batch
- `unsupported_shape` — input object missing required attrs

### 4. Confidence scores
| Source | Confidence | Rationale |
|--------|-----------|-----------|
| CT | 0.6 | Authoritative but potentially stale certs |
| Wayback Diff | 0.75 | Provenance from archive, authoritative timestamps |
| PassiveDNS | 0.5 | Passive observation, no active validation |

### 5. MAX_BRIDGE_OUTPUT = 500
Output capped per batch call, matching the DuckDB write convention.

---

## Rejection Reason Constants

| Constant | Value |
|----------|-------|
| `REJECTION_MISSING_DOMAIN` | `"missing_domain"` |
| `REJECTION_MISSING_VALUE` | `"missing_value"` |
| `REJECTION_LOW_INFORMATION` | `"low_information"` |
| `REJECTION_DUPLICATE_CANDIDATE` | `"duplicate_candidate"` |
| `REJECTION_UNSUPPORTED_SHAPE` | `"unsupported_shape"` |

---

## Test Summary (32 tests)

| Class | Tests | Status |
|-------|-------|--------|
| `TestCTResultsToFindings` | 11 | PASS |
| `TestWaybackResultsToFindings` | 8 | PASS |
| `TestPassiveDNSResultsToFindings` | 8 | PASS |
| `TestNonfeedFindingBridgeBoundaries` | 5 | PASS |

### Boundary Verification
- **No scheduler import**: Verified via module introspection
- **No graph write**: Verified via AST scan (zero graph calls)
- **No hash() builtin**: Verified via AST scan + deterministic ID test
- **No live network**: Verified via AST scan (zero network imports in test module)
- **Output bounded**: Verified: MAX_BRIDGE_OUTPUT=500 enforced

---

## Files Created

```
probe_f207j_nonfeed_finding_bridge/
├── __init__.py                  # Sprint namespace + all exports
├── nonfeed_finding_bridge.py    # Conversion helpers + rejection constants
└── REPORT_NONFEED_FINDING_BRIDGE.md  # This report

tests/probe_f207j_nonfeed_finding_bridge/
├── __init__.py
└── test_nonfeed_finding_bridge.py  # 32 tests
```

---

## Abort Condition Checklist

| Condition | Status |
|-----------|--------|
| Scheduler edit | NOT EDITED |
| Live network | NOT CALLED |
| DB write | NONE |
| Graph direct write | NONE |
| New storage authority | NONE |
| Stealth enablement | NONE |

---

## Verification Commands

```bash
# Run F207J-B tests
python -m pytest tests/probe_f207j_nonfeed_finding_bridge/ -v

# Run F207J-B + F207F
python -m pytest tests/probe_f207j_nonfeed_finding_bridge tests/probe_f207f_ct_wayback_pdns -q

# All tests pass: 32 + 18 = 50 passed
```

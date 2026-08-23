# tool-sprint-gate

**Type:** Tool Suite  
**Path:** `tools/sprint_gate/`  
**Status:** current

## Purpose

Sprint quality gates for pre-flight and post-flight validation.

## Gate Files

| Gate | Purpose |
|------|---------|
| `prelive_artifact_pack.py` | Package artifacts for prelive |
| `live_artifact_triage.py` | Triage live artifacts |
| `prelive_decision_gate.py` | Go/no-go decision |
| `live_kpi_responsibility_index.py` | KPI measurement |
| `live_multisource_validator.py` | Multi-source validation |
| `core_readiness_gate.py` | Core readiness check |
| `live_memory_preflight.py` | Memory budget check |

## Invariants

- [TSG-1] All gates must pass for sprint completion
- [TSG-2] Gate failures logged with F-code
- [TSG-3] Skip with `--force` flag

## Dependencies

- `duckdb` for artifact queries
- `psutil` for resource checks

# Evidence Ledger — Architecture Reference

> **Source:** `evidence_log.py` module docstring (extracted 2026-07-29).
> Extracted from `"""..."""` block to keep in-file docstring ≤ 30 lines.

## Role

Append-only evidence ledger for the autonomous research system.
Implements the **EVIDENCE LEDGER** boundary — records what happened during
research but does NOT govern sprint truth or own facts.

## Facts / Ledger / Derived Map

### Tier 1 — Evidence Ledger (`EvidenceLog`)

- **Append-only events:** `tool_call`, `observation`, `synthesis`, `error`, `decision`, `evidence_packet`
- **Hash-chained events** with tamper detection
- **Ring buffer in RAM** (max 100 events) + SQLite/JSONL persistence

### Tier 2 — Sprint Facts (`DuckDBShadowStore`)

- `sprint_delta`, `sprint_scorecard`, `source_hit_log` — canonical sprint metrics
- `shadow_findings`, `shadow_runs` — finding-level forwarded from EvidenceLog

### Tier 3 — Graph/Store (injected)

- `IOCGraph` (Kuzu), `SemanticStore` (LanceDB), `DuckPGQGraph` (analytics donor)

## Ledger → Facts Boundary (Sprint F11C)

```
ResearchContext (carrier) --handoff metadata--> EvidenceLog (ledger writer)
EvidenceLog.append() --analytics_hook--> DuckDBShadowStore (sprint facts)
```

The handoff flows through:
1. `ResearchContext.context_metadata` carries `ContextHandoffMetadata` descriptor
2. `EvidenceLog.create_event(correlation=)` receives `RunCorrelation` dict
3. Shadow `analytics_hook` receives correlation via `payload["_correlation"]`

## Ledger Boundary Rules

| # | Rule |
|---|------|
| [1] | EvidenceLog remains ledger **WRITER** — no orchestrator authority |
| [2] | ResearchContext remains context **CARRIER** — no writer authority |
| [3] | Correlation is the **ONLY** cross-boundary handoff mechanism |
| [4] | `context_metadata` is carrier-internal (EvidenceLog never reads it directly) |
| [5] | No new session manager or persistence redesign |

⚠️ This module does NOT own sprint facts or derived views.
It is the **EVIDENCE LEDGER** — the immutable record of what happened.

## M1 8GB Optimizations

- Ring buffer in RAM (max 100 events)
- Append-only JSONL persistence to disk
- Trimmed payloads (no full texts)
- Automatic log rotation

## See Also

- `knowledge/duckdb_store.py` — `DuckDBShadowStore` (sprint facts authority)
- `graph/quantum_pathfinder.py` — `DuckPGQGraph` (analytics donor)
- `brain/research_hypothesis_engine.py` — hypothesis generation

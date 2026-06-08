# 0001 — Record architecture decisions

## Status

Accepted (template, established 2026-06-06).

## Context

We need to record architecturally significant decisions: model choices, storage layouts,
sprint-lifecycle changes, sidecar activations, transport policies, M1-bound overrides.
Without a record, the rationale for "why is it this way" gets lost across sprints.

## Decision

We use MADR 3.0 (Markdown Any Decision Records) for ADRs in this repo:

- One file per decision, immutable once accepted (superseded by a new ADR).
- Filename: `NNNN-kebab-case-title.md`, numbered sequentially from `0001`.
- This file (`0001-record-architecture-decisions.md`) is the **only** template —
  copy its structure for new ADRs.
- Status lifecycle: `Proposed` → `Accepted` → `Superseded by NNNN` | `Deprecated`.

## Consequences

- New ADRs go in `docs/adr/` (single-context layout, per `docs/agents/domain.md`).
- Skills that need to consult past decisions read this directory at the start of work
  (see `docs/agents/domain.md` §"Flag ADR conflicts").
- Superseded ADRs stay on disk; the successor ADR cross-references them.
- ADRs are **not** commit messages. ADRs explain *why* a class of decisions is the
  way it is; commit messages explain *what* changed in a single commit.

## Template (copy from here for new ADRs)

```markdown
# NNNN — <Title>

## Status

<Proposed | Accepted | Superseded by NNNN | Deprecated>

## Context

<What is the issue we're seeing that motivates this decision? Include hardware
constraints, deadlines, prior decisions being revisited.>

## Decision

<What did we decide? One paragraph, declarative voice.>

## Consequences

<What becomes easier? What becomes harder? What trade-offs are now locked in?>
```

## First concrete decision to capture

The next sprint that introduces an architecturally significant change (storage
migration, new transport policy, M1 budget override, sidecar promotion from
advisory to canonical) should add `0002-…md` here.

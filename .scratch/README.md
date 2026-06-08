# `.scratch/` — local issue tracker

This directory is the **local markdown issue tracker** for the
hledac/universal repo. See `docs/agents/issue-tracker.md` for the full
convention; this README is a one-screen summary.

## Layout

```
.scratch/
├── README.md                         ← you are here
└── <feature-slug>/                   ← one directory per feature
    ├── PRD.md                        ← Product Requirements Document
    └── issues/
        ├── 01-<slug>.md              ← first implementation issue
        ├── 02-<slug>.md
        └── …
```

## Per-issue header (template)

```markdown
# <NN> — <slug>

Status: <needs-triage | needs-info | ready-for-agent | ready-for-human | wontfix>
Created: YYYY-MM-DD
Sprint: <F-number or "n/a">

## Task

<one paragraph — what needs to happen, with file:line references>

## Acceptance criteria

- [ ] …
- [ ] …

## Comments

<!-- append conversational history below this line -->
```

## Status values

Mapované 1:1 na kanonické role z `docs/agents/triage-labels.md`:

| `Status:` value | Význam |
|---|---|
| `needs-triage` | maintainer musí vyhodnotit |
| `needs-info` | čeká se na doplnění od reportéra |
| `ready-for-agent` | plně specifikováno, AFK agent může vzít |
| `ready-for-human` | vyžaduje lidskou implementaci |
| `wontfix` | nebude řešeno |

## When does this directory get created?

První `to-issues` nebo `to-prd` skill, který publikuje issue, vytvoří příslušnou
`feature-slug` podsložku automaticky. Tento `README.md` je bootstrapping, aby
adresář existoval v repozitáři a GitHub remote ho viděl.

## What does NOT go here

- **Architektura** → `docs/ARCHITECTURE.md` + `docs/architecture/`
- **Doménový glosář** → `CONTEXT.md`
- **Architektonická rozhodnutí** → `docs/adr/`
- **Code review a audit zprávy** → `docs/audits/`
- **Sprint snapshoty** → `graphify-out/<YYYY-MM-DD>/`

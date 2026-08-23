# Tree Ring Memory Agent Instructions

This directory contains Tree Ring Memory's local project memory store.

Memory is a recall aid. It does not replace project source files, tests, root
`AGENTS.md` files, explicit user instructions, DOX contracts, or other local
project documentation.

## Memory Root

```text
.tree-ring
```

Do not commit local memory databases or exports unless the project explicitly
requires sanitized fixtures.

## Agent Skill

Read `SKILL.md` before using Tree Ring Memory. It explains when to recall,
remember, redact, forget, consolidate, and avoid memory capture.

## CLI Reference

Read `CLI.md` for local commands. Common commands:

```bash
tree-ring --root .tree-ring recall "project startup warnings"
tree-ring --root .tree-ring remember "Use project-scoped recall before risky changes." --event-type lesson --scope project
tree-ring --root .tree-ring evidence "A promoted evaluation fixed stale state." --outcome promoted --evidence-ref evals/run-042 --score 0.91
tree-ring --root .tree-ring forget mem_example --mode redact --reason "remove sensitive detail"
tree-ring --root .tree-ring dox sync --source-root . --dry-run
tree-ring --root .tree-ring revolve sync --source-root revolve --dry-run
tree-ring --root .tree-ring integrations scan --source-root .
tree-ring --root .tree-ring tui
```

## Harness Bridges

Harness-native bridge files should point back to this directory instead of
copying memory data. Recommended project bridges include
`.agents/skills/tree-ring-memory/SKILL.md` for Codex/Gemini-style skill loaders,
`.claude/skills/tree-ring-memory/SKILL.md` plus `CLAUDE.md` references for
Claude Code, root `AGENTS.md` references for OpenCode/DOX-style agents, and
`.pi/settings.json` resource references for Pi.

Project-level bridges are preferred because they stay scoped to the current
repo. Global bridges affect every project and should be treated as explicit
user opt-in configuration.

Tree Ring Memory is agent-mediated. Bridge files tell the active agent when to
call `tree-ring recall`, `tree-ring remember`, `tree-ring evidence`,
`tree-ring forget`, `tree-ring consolidate --dry-run`, or `tree-ring maintain`.
They do not authorize hidden transcript scraping or autonomous durable writes.

## Multi-Agent Coordination

For same-host fan-out/fan-in, give every worker a distinct `--agent-profile`,
share one `--workflow-id`, use a new `--session-id` for each attempt, and attach
a stable `--operation-id` plus `--source-ref` to every logical write. At fan-in,
recall with the workflow, session, and intended scope before creating a
source-linked shared summary.

Scope and identity fields partition and route local memory; they are not
read access-control boundaries. A shared SQLite root is for concurrent processes
on one host using a local filesystem. Cross-host or network-filesystem workflows
should use per-host stores and an explicit evidence-preserving fan-in.

## Coordinated Write Policy

Stores default to backward-compatible Open mode. A coordinator may opt a store
into Coordinated mode:

```bash
tree-ring --root .tree-ring policy enable --coordinator release-coordinator
export TREE_RING_COORDINATOR_TOKEN='<one-time capability printed by enable>'
tree-ring --root .tree-ring policy status
tree-ring --root .tree-ring policy audit --limit 100
tree-ring --root .tree-ring policy rotate --coordinator release-coordinator-next
tree-ring --root .tree-ring policy disable
unset TREE_RING_COORDINATOR_TOKEN
```

Replace the environment value with the new one-time capability immediately
after rotation. Never pass the capability as a CLI flag or retain it in memory
events, logs, source refs, or committed files. Tree Ring stores only its hash.
Inject the variable only into coordinator processes, and keep it unset in every
ordinary worker environment.

In Coordinated mode, an ordinary worker may create only non-heartwood
`scope=agent` memory whose `agent_profile` matches the write context supplied by
`--agent-profile` or `TREE_RING_AGENT_PROFILE`. Shared or non-agent writes,
heartwood, imports, DOX/Revolve persistence, consolidation, ring changes,
supersede/delete/redact, and applied maintenance require the coordinator
capability. Status, audit, recall, export, adapter dry-runs, consolidation
dry-runs, and report-only maintenance remain read-only.

For TUI worker writes, pass `--agent-profile <worker>` to
`tree-ring --root .tree-ring tui` (or set `TREE_RING_AGENT_PROFILE`); `/remember`
then creates agent scope. Lifecycle actions such as promote, scar, seed,
supersede, forget, redact, and persisted consolidation require
`TREE_RING_COORDINATOR_TOKEN`.

This policy is operational authorization in official Rust/CLI write paths. It
is not a read ACL, an OS security boundary, or protection against an adversary
who controls the database files or process environment. The supported shared
root remains one host and a local filesystem.

Before a v0.13 binary first upgrades a store to schema v3, stop every Tree Ring
process, checkpoint and back up the database, and upgrade every CLI, plugin,
and bundled worker before reopening it. Schema v3 rejects memory inserts,
updates, and deletes from old v0.12 writers; all mixed-version operation is
unsupported. Roll back only by stopping all processes and restoring the
pre-upgrade backup.

## Harness Preflight

Before substantive project work in a new harness session, read this canonical
guidance and run the project-local preflight with harness-derived identity:

```bash
tree-ring --root .tree-ring integrations preflight --harness codex --agent-profile <worker> --workflow-id <workflow> --session-id <session> --context-format json
```

If adapter preflight is unavailable, use the safe fallback
`tree-ring --root .tree-ring recall "project startup constraints"`; this
fallback is useful context but does not create activation proof. A matching
receipt proves that scoped preflight recall ran for that session. It does not
prove durable memory creation and is not an adversarial security boundary.

## Memory Quality Gates

Recall gates:

- Before substantial project work, recall project constraints, scars, user preferences, and unresolved seeds.
- Before risky changes, recall warnings and evidence-linked prior failures.
- Before repeating a workflow, recall prior errors and accepted procedures.
- Before closeout, recall recent decisions so memory updates do not contradict already-stored lessons.

Trust gates:

- Prefer source-linked, non-superseded, high-confidence memories.
- Re-read source files, tests, explicit user instructions, DOX contracts, or Revolve evidence when memory conflicts with current sources.
- Do not treat sensitive or hidden-by-default memory as ordinary recall context.

Write gates:

- Remember only durable decisions, validated lessons, reusable warnings, corrections, future seeds, and evidence-backed outcomes.
- Reject transient planning chatter, duplicate wording, tool noise, and unsupported claims.
- Require evidence refs for promoted or rejected evaluated outcomes.
- Require user confirmation before creating or promoting broad cross-project heartwood.

## DOX Integration

If this project uses DOX-style `AGENTS.md` traversal, merge the relevant
sections from the template below into the project root `AGENTS.md`. Tree Ring
Memory does not overwrite root project contracts automatically.

Use `tree-ring --root .tree-ring dox sync --source-root . --dry-run` to preview
concise memory summaries for local `AGENTS.md` files. The source contracts
remain authoritative; memory is only a recall aid.

## Revolve And Evidence Integration

Use `tree-ring --root .tree-ring evidence ... --evidence-ref <ref>` for individual
evaluated outcomes. Use the Revolve sync command below to preview source-linked
memories from Revolve/evaluation records:

```bash
tree-ring --root .tree-ring revolve sync --source-root revolve --dry-run
```

Promoted outcomes become heartwood, rejected outcomes become scars, deferred
outcomes become seeds, and observed outcomes become outer-ring evidence.

---

# Tree Ring Memory Project Contract

This file is a DOX-style template for projects that use Tree Ring Memory.
Copy it into a project as `AGENTS.md` or merge the relevant sections into an existing project instruction file.

## Authority

Project source files, tests, local `AGENTS.md` files, and explicit user instructions remain authoritative.
Tree Ring Memory is a recall aid. It must not replace reading the local project contract.

## Memory Store

Default local memory path:

```text
.tree-ring/
```

Do not commit local memory databases or exports unless the project explicitly requires sanitized fixtures.

Harness-native bridge files should point agents back to this memory root's
generated guidance instead of duplicating memory data. Prefer project-level
bridges for the current repo. Treat global Tree Ring bridges as explicit user
configuration that affects every project.

## Recall Rules

Before substantial work, recall project-scoped memory for:

- current project conventions
- prior decisions
- durable user preferences
- warnings and scars related to the task
- unresolved seeds that may affect the plan

Prefer narrow recall queries that include the project name, subsystem, file path, or workflow.

## Remember Rules

Remember only meaningful information:

- durable decisions
- verified lessons
- user corrections
- repeated workflow warnings
- project conventions
- future seeds

Do not store full transcripts, scratchpad notes, raw chain-of-thought, secrets, credentials, or sensitive personal details.

Use `tree-ring evidence` for evaluated outcomes from runs, checkpoints,
experiments, incidents, PRs, issues, or reviewed artifacts. Promotions should
become heartwood only when the evidence supports durable reuse. Rejections with
reusable warning value should become scars. Deferred possibilities should become
seeds.

Use source adapters when local authoritative files already contain the guidance
or evaluated outcome:

```bash
tree-ring dox sync --source-root . --dry-run
tree-ring revolve sync --source-root revolve --dry-run
tree-ring integrations scan --source-root .
```

Run adapter syncs as previews first. Persist only concise, source-linked
summaries that help future recall.

## Agent-Mediated Updates

Tree Ring Memory writes durable memories only when a user, agent, adapter,
import, TUI action, consolidation command, or explicit maintenance command calls
the CLI. It must not be used as a hidden transcript recorder. Bridge files tell
the active agent when to call Tree Ring; they do not authorize autonomous
background capture.

## Multi-Agent Coordination

When multiple local workers share this memory root:

- Give every worker a unique `agent_profile`.
- Share one `workflow_id` across the fan-out/fan-in.
- Use one `session_id` for each genuine execution attempt; exact retries reuse
  the original session ID.
- Give each logical write a stable unique `operation_id` and a source reference.
- Use `scope=agent` only with `agent_profile`, `scope=workflow` only with
  `workflow_id`, and `scope=session` only with `session_id`.
- Recall at fan-in with explicit workflow, session, and scope filters. Omit the
  agent-profile filter when the coordinator needs every worker.
- Treat project and global memories as shared. Do not attribute a shared summary
  to one worker unless every source has that producer identity.

`TREE_RING_AGENT_PROFILE`, `TREE_RING_WORKFLOW_ID`, and
`TREE_RING_SESSION_ID` can provide the matching CLI defaults. An exact retry of
the same operation and payload returns the original memory; conflicting reuse
of the operation key must fail. Replaced operation namespaces and redacted
memory IDs stay claimed until explicit hard deletion.

Scope and identity fields are routing partitions, not read authorization
boundaries. A same-user coordinator with filesystem access can recall across
worker profiles.

This shared-root contract covers concurrent processes on one host and a local
filesystem. It is not a distributed lock service and does not claim safe
cross-host or NFS database sharing.

## Coordinated Store Policy

Stores default to backward-compatible Open mode. When only a designated
coordinator should publish or mutate shared memory, enable the optional
Coordinated policy:

```bash
tree-ring --root .tree-ring policy enable --coordinator release-coordinator
export TREE_RING_COORDINATOR_TOKEN='<one-time capability printed by enable>'
tree-ring --root .tree-ring policy status
tree-ring --root .tree-ring policy audit --limit 100
```

Never pass the capability as a CLI flag or store it in memory, logs, source
refs, or committed files. Tree Ring prints it once and stores only its hash.
Inject it only into coordinator processes; launch ordinary workers with
`TREE_RING_COORDINATOR_TOKEN` unset.

In Coordinated mode, an ordinary worker may create only non-heartwood
`scope=agent` memory whose `agent_profile` matches `--agent-profile` or
`TREE_RING_AGENT_PROFILE`. Project/global/workflow/session writes, heartwood,
imports, persisted DOX/Revolve sync, persisted consolidation, ring changes,
supersede/delete/redact, and applied maintenance require
`TREE_RING_COORDINATOR_TOKEN`. Read-only recall/export, policy status/audit,
adapter and consolidation dry-runs, and report-only maintenance remain
available without it.

For the TUI, set `--agent-profile <worker>` or
`TREE_RING_AGENT_PROFILE=<worker>` so `/remember` defaults to agent scope.
Lifecycle actions require the coordinator capability.

Rotate and disable only while the current capability is exported:

```bash
tree-ring --root .tree-ring policy rotate --coordinator release-coordinator-next
export TREE_RING_COORDINATOR_TOKEN='<new one-time capability>'
tree-ring --root .tree-ring policy disable
unset TREE_RING_COORDINATOR_TOKEN
```

This is operational authorization in official Rust/CLI write paths, not a read
ACL or protection against an adversary who controls local files or the process
environment.

Before a v0.13/schema-v3 upgrade, stop all Tree Ring processes, checkpoint and
back up the store, and upgrade every CLI, plugin, and bundled worker before
reopening it. Schema v3 fences memory inserts, updates, and deletes from old
v0.12 writers; all mixed-version operation is unsupported. Roll back only by
restoring the pre-upgrade backup.

## Ring Mapping

- Use `cambium` for active task context.
- Use `outer` for recent project lessons.
- Use `inner` for compressed older project knowledge.
- Use `heartwood` for confirmed durable rules and preferences.
- Use `scar` for failures, regressions, rejected approaches, and security or privacy warnings.
- Use `seed` for future work and unresolved hypotheses.

## Sensitive Data

Secrets and credentials must not be stored.
If a useful lesson involves sensitive data, store a redacted summary and source pointer only.

## Forgetting

If a memory is incorrect, stale, sensitive, or superseded, delete, redact, or supersede it with an explicit reason.

## Source Discipline

Memory summaries should point back to source evidence such as:

- file paths
- PRs or issues
- tests
- evaluation runs
- local project docs

When source documents and memory disagree, re-read the source documents and update or forget the stale memory.

DOX-style `AGENTS.md` files and Revolve/evaluation records remain
authoritative. Tree Ring Memory can summarize and point to them, but it must not
replace DOX traversal, copy whole contract trees, treat stale scores as current
truth, or promote an outcome without source evidence.


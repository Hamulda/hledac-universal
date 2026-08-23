# Tree Ring Memory CLI Quick Reference

Tree Ring Memory is a local-first memory lifecycle layer for AI agents.

Use memory deliberately:

- recall before substantial project work
- remember durable decisions, lessons, warnings, and user preferences
- use scars for failures that should not be repeated
- use seeds for future work and hypotheses
- forget, redact, or supersede stale or sensitive memory

Core commands:

```bash
tree-ring init
tree-ring recall "project startup warnings"
tree-ring remember "Use project-scoped recall before risky changes." --event-type lesson --scope project
tree-ring evidence "Snapshot invalidation fixed stale unread chat state." --outcome promoted --evidence-ref evals/chat-state/run-042 --score 0.91
tree-ring evidence "Aggressive caching caused stale multi-chat state." --outcome rejected --evidence-ref evals/cache-branch/run-013
tree-ring forget mem_example --mode redact --reason "remove sensitive detail"
tree-ring export --output memories.jsonl
tree-ring import memories.jsonl --dry-run
tree-ring audit --audit-type all
tree-ring consolidate --period-type manual --dry-run
tree-ring maintain
tree-ring dox sync --source-root . --dry-run
tree-ring revolve sync --source-root revolve --dry-run
tree-ring integrations scan --source-root .
tree-ring policy status
tree-ring tui
```

Project bridge files:

- keep `.tree-ring/SKILL.md` and `.tree-ring/CLI.md` canonical
- point harness-native files at those generated references
- prefer project-level bridges over global bridges
- treat global bridges as explicit opt-in user configuration

Adapter rules:

- `tree-ring dox sync` summarizes `AGENTS.md` files and keeps source contracts authoritative.
- `tree-ring revolve sync` imports promoted, rejected, deferred, or observed evidence records without replacing Revolve/evaluation docs.
- `tree-ring evidence` records individual evaluated outcomes with an explicit source ref.
- Run adapter commands with `--dry-run` before writing memory.
- `tree-ring integrations scan` is read-only; add harness bridge references manually until a link command is available.

Multi-agent coordination:

- Give each worker a distinct `--agent-profile`, share one `--workflow-id`, and use a new `--session-id` for each execution attempt.
- Give every logical write a stable `--operation-id` and a durable `--source-ref`; exact retries return the original memory, while conflicting reuse fails closed.
- At fan-in, recall with the shared workflow/session and an explicit scope. Scope and identity fields partition and route local memory; they are not access-control boundaries.
- A shared SQLite root supports concurrent processes on one host and a local filesystem. Use per-host stores plus an explicit source-preserving fan-in for cross-host or network-filesystem workflows.

Coordinated write policy:

- Stores remain in backward-compatible Open mode until a coordinator explicitly runs `tree-ring policy enable --coordinator <label>`.
- Enable and rotate print a one-time capability. Put it only in `TREE_RING_COORDINATOR_TOKEN`; there is no token CLI flag, and Tree Ring never stores the plaintext capability. Inject it only into coordinator processes and keep it unset for ordinary workers.
- In Coordinated mode, an ordinary worker may create only non-heartwood `scope=agent` memory whose `agent_profile` matches its `--agent-profile` or `TREE_RING_AGENT_PROFILE`.
- Shared or non-agent writes, heartwood, imports, DOX/Revolve persistence, consolidation, ring changes, supersede/delete/redact, and applied maintenance require the coordinator capability.
- `tree-ring policy status` and `tree-ring policy audit --limit 100` are read-only. Rotate with `tree-ring policy rotate --coordinator <label>` and return to Open mode with `tree-ring policy disable`; both require the current capability.
- This is operational write authorization in official Rust/CLI paths, not a read ACL or protection against an adversary who controls the local files or process environment.
- Before opening a pre-v0.13 store with a v0.13/schema-v3 binary, stop every Tree Ring process, checkpoint and back up the store, and upgrade every CLI, plugin, and bundled worker. Schema v3 fences memory inserts, updates, and deletes from old v0.12 writers; all mixed-version operation is unsupported. Roll back only by restoring the backup.

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

Memory quality gates:

- Before substantial project work, recall project constraints, scars, user preferences, and unresolved seeds.
- Before risky changes, recall warnings and evidence-linked prior failures.
- Before repeating a workflow, recall prior errors and accepted procedures.
- Before closeout, recall recent decisions so memory updates do not contradict already-stored lessons.
- Before trusting memory, prefer source-linked, non-superseded, high-confidence results.
- Re-read source files, tests, explicit user instructions, DOX contracts, or Revolve evidence when memory conflicts with current sources.
- Before writing memory, reject transient planning chatter, duplicate wording, tool noise, and unsupported claims.
- Require evidence refs for promoted or rejected evaluated outcomes.
- Require user confirmation before creating or promoting broad cross-project heartwood.

Safety rules:

- Do not store secrets, credentials, private keys, or raw chain-of-thought.
- Prefer concise, source-linked summaries over transcript capture.
- Do not scrape chats or turn TUI event-stream pulses into durable memory without an explicit write command.
- Treat local source files, tests, explicit user instructions, and root `AGENTS.md` files as authoritative.
- When memory and source docs disagree, re-read source docs and update or forget stale memory.

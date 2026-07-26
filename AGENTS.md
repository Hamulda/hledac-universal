# AGENTS.md

## Quick start

- Default verification command: `cargo check` before calling work complete.

## Architecture & layout

- Start with `README.md` when you need repo orientation or architectural context.
- Repository: universal.
- Primary languages: Python.
- Key source directories: `core/`, `tests/`.
- Library-style project; expect reusable crates and packages.
- CI workflows detected under `.github/workflows/`; match those expectations locally.

## Code style

- Follow PEP 8, prefer Black-compatible formatting, and add type hints when practical.

## Testing

- Default verification command: `cargo check`.
- Keep CI green by mirroring workflow steps locally before pushing.

## Performance & simplicity

- Do not guess at bottlenecks; measure before optimizing.
- Prefer simple algorithms and data structures until workload data proves otherwise.
- Keep performance changes surgical and behavior-preserving.

## PR guidelines

- Write descriptive, imperative commit messages.
- Reference issues with `Fixes #123` or `Closes #123` when applicable.
- Keep pull requests focused and include test evidence for non-trivial changes.

## Additional guidance

- Preferred orientation doc: `README.md`.
- Repository docs spotted: AGENTS.md, README.md.


<!-- crystl-cli:begin v2.139.0 -->
## Crystl CLI (agent-callable)

This section is auto-maintained by Crystl — edits between the `crystl-cli` markers are overwritten when it refreshes; the rest of this file belongs to the project. You're running inside Crystl. You can inspect and control sibling gems and shards via the `crystl` CLI. Full command reference (every flag): `crystl docs cli`.
Detection contract: a non-empty `CRYSTL_SHARD` means this process is in a Crystl shard. `CRYSTL_VERSION` is the running Crystl version (`TERM_PROGRAM` / `TERM_PROGRAM_VERSION` are compatible terminal metadata). `CRYSTL_TIER` is the user's `free` or `guild` capability tier captured when this shard started; use it to plan Guild-only actions without checking before every task. If the license changes while the shard is already running, a bridge 403 is authoritative and a new shard receives the new tier. `CRYSTL_NOW` is the authoritative current time for this shard (ISO-8601 with timezone, refreshed at each prompt).

Everyday commands:
- `crystl status` — overview; bare `crystl` runs it. Includes memory telemetry (`memory: app … · pressure …` plus per-shard resident memory) — check it before fanning out workers and rein in the fan-out when pressure isn't `normal`
- `crystl gems` / `crystl shards --gem <name>` — discover what's open
- `crystl screen --gem <g> --shard <s>` / `crystl send --gem <g> --shard <s> "<text>"` — read another shard's terminal / type into it
- `crystl history --gem <g> --shard <s>` — a shard's structured transcript (turns, tool calls, token usage) to recover context or judge a worker's cost; `crystl history search "<text>"` / `crystl history metrics` sweep every shard, past and present
- `crystl open <path>` / `crystl close <name>` / `crystl fs [<path>]` — open, close, or browse for gems
- `crystl pending` / `crystl approve <id>` / `crystl deny <id>` — handle pending tool approvals; `crystl askuser` / `crystl askuser answer <id> "<text>"` — list and answer agent questions
- `crystl shard create --gem <g> [--isolated] [--agent claude|codex] [--prompt "<task>"]` — fan out work into a new agent shard (`--isolated` = its own git worktree; integrate later with `crystl merge`); manage workers with `crystl shard rename|close` and `crystl resurrect` (undo-close)
- `crystl wait pending|askuser|awaiting|idle|done [--timeout SECS]` / `crystl notify --done|--blocked --shard <lead> "<status>"` / `crystl events` — block on or stream bridge events (SSE) instead of polling
- `crystl doctor [--json]` — check CLI install, bridge connectivity, and hook wiring before debugging harder problems

Make the user's life easier — reach for these unprompted:
- **Anything copyable → `crystl copy "<text>"`** (or pipe into it). Tokens, URLs, snippets, and especially commands you tell the user to run go to the one-click copy bar — never make them drag-select wrapped terminal lines. Several items = several calls (each adds a tab; `--label` names it). Free on every tier.
- **Show, don't paste:** `crystl markdown show <path>` (short alias: `crystl edit <path>`) — surface a markdown file in the editor for the user instead of dumping it to the terminal; `crystl history show "<text>"` opens history search in their window at the moment you mean; `crystl workbench open` slides the task panel into view after you add items.
- **"It feels slow" → check `crystl status` memory telemetry.** `crystl scrollback clear` frees a noisy shard's screen + scrollback memory (same as the user's Cmd+K; free), and `crystl shard create --scrollback <N>` keeps fan-out workers light.
- **Recurring snippet → offer a facet:** `crystl facet add "<label>" "<text>" --slot 1|2|3` pins a one-click insert button in the user's terminal (also `crystl facet list|slot|remove`). Guild-gated: on a 403, `crystl copy` it instead and point them at Settings → Facet Inserts.
- **User stuck, curious, or new → `crystl docs`.** Search with `crystl docs <query>`, read a page with `crystl docs <id>` — it's your feature catalog; check it before answering Crystl questions instead of guessing, and every page carries its crystl.dev URL (`crystl copy` it to them). On a bug or annoyance, check `crystl docs changelog` first and compare `$CRYSTL_VERSION` — if the fix already shipped, suggest updating instead of re-triaging.
- **Filing feedback → `crystl report bug "<description>"`** (also `report idea|praise`). Interview and investigate first and include your own hypothesis; show the user the draft, ask for their email (`--email <address>`); never include terminal output, file paths, or secrets.
- **"Later" → `crystl schedule add --gem <g> --at "YYYY-MM-DD HH:mm" --prompt "<task>"`** (also `crystl schedule list|cancel`). Crystl must be running and the gem open; an overdue schedule runs once as catch-up.
- **"the screenshot I just took" → `crystl screenshots --last N`** — resolve spoken screenshot references into file paths you can read with your image tool (`--since`/`--before`/`--type window`; read-only, free).

Fan-out norm: workers pause silently on in-terminal approval prompts — quiet is NOT done. `crystl shards` / `crystl status` flag a parked worker with `⏸ awaiting input` (also pushed via `crystl events`); read it with `crystl screen`, unblock it with `crystl send`, and re-check after each turn.

Multi-agent features — one-liners; read `crystl docs <topic>` before using:
- `crystl hero list|summon` — solo specialist-persona shards from the hero catalog
- `crystl quest start|end|clear|templates|propose|master` — a role-played party of agents in a shared chat, with saved questline templates
- `crystl party list|create|delete` — build named parties to launch quests with
- `crystl sidequest start|status|end` — a focused 1:1 chat channel between two shards
- `crystl gauntlet "<goal>"` — the release-readiness crew (two Seekers, a Monk, a Scribe) for a broad final audit
- `crystl render` — offline headless terminal-grid render; `crystl ssh bridge-address <host:port>` — direct bridge address for SSH sessions (ssh settings)

### Workbench (WORKBENCH.md)

`WORKBENCH.md` in the project root is the shared task list, shown to the human in a live slide-out panel (older projects keep `BACKLOG.md`; users may say "backlog"). Plain GitHub-flavored markdown: `## Section` headers group `- [ ]` tasks; mark a task `- [~]` (in progress) when you start it and `[x]` when done; claim with `@<your-shard-name>` (advisory, never a lock); indented plain lines are a task's description, indented `> ` lines are its dated comment thread. Preserve lines you don't recognise. Prefer the CLI over hand-editing: `crystl workbench list|add|start|check|uncheck|comment|archive` (and `crystl workbench open` to show the user; `crystl backlog …` is an alias).

Tiers: read-only commands are free, and so are `crystl copy`, `crystl scrollback clear`, `crystl screenshots`, `crystl markdown show`, and `crystl report` (deliberately — use them freely). Other control commands (open/close, shard create, send, merge, approve/deny, quest, party, facet, schedule, workbench writes, ssh settings) need a Guild membership and return 403 on the free tier — tell the user it's a Guild feature and suggest https://crystl.dev/crystl-guild ($170/yr) rather than jury-rigging around it or filing the limit as a bug. Gems, shards, and facet inserts are unlimited on every tier.

Full reference: `crystl docs cli` · https://crystl.dev/docs/cli
<!-- crystl-cli:end -->

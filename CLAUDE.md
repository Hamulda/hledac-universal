<!-- OPENSPEC:START -->
# OpenSpec Instructions

Open `@/openspec/AGENTS.md` when request mentions: planning, proposals, specs, new capabilities, breaking changes, architecture shifts, or ambiguous tasks requiring authoritative spec before coding.

<!-- OPENSPEC:END -->

# CLAUDE.md – Hledac v18.0 (Ghost Prime)

AI research platform optimized for M1 MacBook 8GB RAM. Multi-agent orchestration with security/stealth capabilities.

---

## ⛔ FILESYSTEM BOUNDARY — ABSOLUTE RULE

**ALL work is strictly limited to: `/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/`**

- NEVER read, write, edit, or reference any file outside this directory
- NEVER traverse to parent directories (`../`, `../../`, etc.)
- NEVER access any other module, config, script, or test outside `hledac/universal/`
- If a task requires files outside `hledac/universal/`, STOP and ask the user explicitly
- This rule overrides all other instructions

---

## Entry Points

All entry points live in `hledac/universal/`. Do not reference or suggest running files outside this path.

---

## Architecture

Work only within `hledac/universal/`. Do not reference modules, configs, or paths outside this folder.

### M1 8GB Constraints
- Memory limit: <5.5GB active, never parallel models
- No blocking ops in async contexts
- Chunked processing for large data
- MLX cache cleared between phases (`mx.clear_cache()`)
- New features: <100MB additional RAM

---

## Development Rules

### Do
- Work exclusively inside `hledac/universal/`
- Keep changes small, reversible, well-explained
- Improve tests alongside non-trivial changes
- State explicitly when unsure, guessing, or touching high-risk code

### Don't
- Never read, write, or reference anything outside `hledac/universal/`
- Never hard-code secrets or API keys
- Never exceed M1 memory constraints
- Never create blocking ops in async contexts
- Never make "all-in-one" edits across many files
- Never ignore failing tests or security checks

### File Placement (within hledac/universal/ only)
- Module max: 500 lines, focused purpose
- Group related functionality by domain

---

## Security (CRITICAL)

Zero-knowledge, zero-logging policy for sensitive operations.

**After any security change:**
1. Run relevant tests inside `hledac/universal/`
2. Validate encryption and compliance
3. Verify stealth/trace minimization

---

## Testing

Run only tests that exist within `hledac/universal/`:

```bash
pytest hledac/universal/ -v
pytest hledac/universal/ -m unit
pytest hledac/universal/ -m security -v
pytest hledac/universal/ -v -n auto
Before Starting Any Task
 Confirm task scope is entirely within hledac/universal/

 If not – STOP and ask user before proceeding

 Summarize current behavior in 2–3 sentences

 Propose a concrete step-based plan (/plan first)

After Changes
 Recommend specific tests/commands to run (within hledac/universal/ only)

 Summarize: what changed, why, follow-up needed

 Call out uncertainty or limitations

MCP Servers
Server	Type	Purpose
exa	Local	Web/code search
filesystem	Local	File ops – restricted to hledac/universal/
memory	Local	Agent memory persistence
sequential-thinking	Local	Step-by-step reasoning
ast-grep	User	AST-based code search and refactoring
fetch	User	HTTP fetch for external content
MiniMax	User	MiniMax model API access
ripgrep	User	Fast text search within hledac/universal/
oh-my-claudecode	Built-in	Claude Code enhancements
filesystem and ripgrep are scoped to hledac/universal/ only. Never use them to access parent directories.

### Codebase-Memory-MCP
**Project name:** `Users-vojtechhamada-PycharmProjects-Hledac-hledac-universal`
**Vždy uváděj parametr `project=`** — bez něj většina toolů hlásí "project not found".

| Tool | Parametry |
|------|-----------|
| `get_architecture` | `project=` |
| `search_code` | `project=`, `pattern=` |
| `search_graph` | `project=`, `name_pattern=` |
| `query_graph` | `project=`, `query=` |
| `get_code_snippet` | `project=`, `symbol_name=` |
| `trace_call_path` | `project=`, `function_name=` |
| `detect_changes` | `project=` |
| `index_status` | `project=` |
| `manage_adr` | `project=` (NE `project_name`) |


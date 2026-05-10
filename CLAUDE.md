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

### Canonical Sprint Owner
`core.__main__.run_sprint()` — sole production sprint owner. All sprint execution flows through this entry point.

### Key Modules

| Module | Purpose |
|--------|---------|
| `brain/` | AI/ML engine: Hermes3Engine, ModelLifecycle, inference, synthesis, hypothesis |
| `coordinators/` | 20+ coordinators: fetch, memory, resource, research, security, performance, multimodal |
| `knowledge/` | Storage: DuckDB (canonical write), LanceDB (RAG), graph_service, semantic_store |
| `pipeline/` | Sprint execution: live_public_pipeline, live_feed_pipeline |
| `runtime/` | Sprint scheduler: SprintScheduler (canonical execution engine) |
| `fetching/` | HTTP transport seam: public_fetcher (curl_cffi, JA3 fingerprint) |
| `transport/` | Multi-transport: httpx, tor, i2p, nym, circuit_breaker |
| `security/` | Cryptography: PQ (ML-DSA-65, HPKE X-Wing), secure_enclave, audit, PII |
| `forensics/` | Metadata extraction, enrichment, steganography |
| `multimodal/` | Vision/OCR: VisionEncoder, MambaFusion, evidence_triage |
| `text/` | Text analysis: hash_identifier, unicode_analyzer, encoding_detector |
| `hypothesis/` | Hypothesis generation: HypothesisGenerator, DempsterShafer, EIGCalculator |
| `export/` | Export: ExportManager, STIX, Markdown, JSON-LD |
| `tools/` | Core utilities: url_dedup (RotatingBloomFilter), lmdb_kv, checkpoint |
| `utils/` | Helpers: async_helpers, bloom_filter, concurrency, mlx_* |

### M1 8GB Constraints
- Memory limit: <5.5GB active, never parallel models
- No blocking ops in async contexts
- Chunked processing for large data
- MLX cache cleared between phases (`mx.clear_cache()`)
- New features: <100MB additional RAM

### HTTP Transport Seams
- `curl_cffi` — primary stealth HTTP (JA3 fingerprint, FetchCoordinator only)
- `httpx` with HTTP/2 — optional transport lane (gated by `HLEDAC_ENABLE_HTTPX_H2`, F206K)
- `aiohttp` — fallback for direct fetches, pastebin_monitor internal session
- Never use aiohttp in FetchCoordinator — only curl_cffi and httpx transport layers

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

<!-- rtk-instructions v2 -->
# RTK (Rust Token Killer) - Token-Optimized Commands

## Golden Rule

**Always prefix commands with `rtk`**. If RTK has a dedicated filter, it uses it. If not, it passes through unchanged. This means RTK is always safe to use.

**Important**: Even in command chains with `&&`, use `rtk`:
```bash
# ❌ Wrong
git add . && git commit -m "msg" && git push

# ✅ Correct
rtk git add . && rtk git commit -m "msg" && rtk git push
```

## RTK Commands by Workflow

### Build & Compile (80-90% savings)
```bash
rtk cargo build         # Cargo build output
rtk cargo check         # Cargo check output
rtk cargo clippy        # Clippy warnings grouped by file (80%)
rtk tsc                 # TypeScript errors grouped by file/code (83%)
rtk lint                # ESLint/Biome violations grouped (84%)
rtk prettier --check    # Files needing format only (70%)
rtk next build          # Next.js build with route metrics (87%)
```

### Test (60-99% savings)
```bash
rtk cargo test          # Cargo test failures only (90%)
rtk go test             # Go test failures only (90%)
rtk jest                # Jest failures only (99.5%)
rtk vitest              # Vitest failures only (99.5%)
rtk playwright test     # Playwright failures only (94%)
rtk pytest              # Python test failures only (90%)
rtk rake test           # Ruby test failures only (90%)
rtk rspec               # RSpec test failures only (60%)
rtk test <cmd>          # Generic test wrapper - failures only
```

### Git (59-80% savings)
```bash
rtk git status          # Compact status
rtk git log             # Compact log (works with all git flags)
rtk git diff            # Compact diff (80%)
rtk git show            # Compact show (80%)
rtk git add             # Ultra-compact confirmations (59%)
rtk git commit          # Ultra-compact confirmations (59%)
rtk git push            # Ultra-compact confirmations
rtk git pull            # Ultra-compact confirmations
rtk git branch          # Compact branch list
rtk git fetch           # Compact fetch
rtk git stash           # Compact stash
rtk git worktree        # Compact worktree
```

Note: Git passthrough works for ALL subcommands, even those not explicitly listed.

### GitHub (26-87% savings)
```bash
rtk gh pr view <num>    # Compact PR view (87%)
rtk gh pr checks        # Compact PR checks (79%)
rtk gh run list         # Compact workflow runs (82%)
rtk gh issue list       # Compact issue list (80%)
rtk gh api              # Compact API responses (26%)
```

### JavaScript/TypeScript Tooling (70-90% savings)
```bash
rtk pnpm list           # Compact dependency tree (70%)
rtk pnpm outdated       # Compact outdated packages (80%)
rtk pnpm install        # Compact install output (90%)
rtk npm run <script>    # Compact npm script output
rtk npx <cmd>           # Compact npx command output
rtk prisma              # Prisma without ASCII art (88%)
```

### Files & Search (60-75% savings)
```bash
rtk ls <path>           # Tree format, compact (65%)
rtk read <file>         # Code reading with filtering (60%)
rtk grep <pattern>      # Search grouped by file (75%). Format flags (-c, -l, -L, -o, -Z) run raw.
rtk find <pattern>      # Find grouped by directory (70%)
```

### Analysis & Debug (70-90% savings)
```bash
rtk err <cmd>           # Filter errors only from any command
rtk log <file>          # Deduplicated logs with counts
rtk json <file>         # JSON structure without values
rtk deps                # Dependency overview
rtk env                 # Environment variables compact
rtk summary <cmd>       # Smart summary of command output
rtk diff                # Ultra-compact diffs
```

### Infrastructure (85% savings)
```bash
rtk docker ps           # Compact container list
rtk docker images       # Compact image list
rtk docker logs <c>     # Deduplicated logs
rtk kubectl get         # Compact resource list
rtk kubectl logs        # Deduplicated pod logs
```

### Network (65-70% savings)
```bash
rtk curl <url>          # Compact HTTP responses (70%)
rtk wget <url>          # Compact download output (65%)
```

### Meta Commands
```bash
rtk gain                # View token savings statistics
rtk gain --history      # View command history with savings
rtk discover            # Analyze Claude Code sessions for missed RTK usage
rtk proxy <cmd>         # Run command without filtering (for debugging)
rtk init                # Add RTK instructions to CLAUDE.md
rtk init --global       # Add RTK to ~/.claude/CLAUDE.md
```

## Token Savings Overview

| Category | Commands | Typical Savings |
|----------|----------|-----------------|
| Tests | vitest, playwright, cargo test | 90-99% |
| Build | next, tsc, lint, prettier | 70-87% |
| Git | status, log, diff, add, commit | 59-80% |
| GitHub | gh pr, gh run, gh issue | 26-87% |
| Package Managers | pnpm, npm, npx | 70-90% |
| Files | ls, read, grep, find | 60-75% |
| Infrastructure | docker, kubectl | 85% |
| Network | curl, wget | 65-70% |

Overall average: **60-90% token reduction** on common development operations.
<!-- /rtk-instructions -->
# Welcome to Hledac OSINT

## How We Use Claude

Based on Vojtech Hamada's usage over the last 30 days:

Work Type Breakdown:
  Build Feature  ████████████████████░░░░░░░░  26%
  Debug & Fix     ███████████████████░░░░░░░░░  28%
  Plan & Design   █████░░░░░░░░░░░░░░░░░░░░░░░  14%
  Improve Quality ████░░░░░░░░░░░░░░░░░░░░░░░░  11%
  Analyze Data    ██░░░░░░░░░░░░░░░░░░░░░░░░░░   7%

Top Skills & Commands:
  /clear                        ████████████████████░░░░░░░░░░░░░░  788x/month
  /context-mode:context-mode    ██████████████░░░░░░░░░░░░░░░░░░░  483x/month
  /effort                       ██████░░░░░░░░░░░░░░░░░░░░░░░░░░  184x/month
  /reload-plugins               █░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   56x/month
  /plugin                       █░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░   55x/month

Top MCP Servers:
  context-mode (plugin)  ██████████████████████████████  15,253 calls
  ripgrep                ███████████░░░░░░░░░░░░░░░░░░   3,468 calls
  filesystem             ██████░░░░░░░░░░░░░░░░░░░░░░░░   1,748 calls
  codebase-memory-mcp    █░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░    170 calls

## Your Setup Checklist

### Codebases
- [ ] hledac/universal — https://github.com/hamulda/new-hledac (main research platform)

### MCP Servers to Activate
- [ ] context-mode (plugin) — Indexed file search and batch execution with FTS5. Auto-indexes command output. Install via `claude plugin install https://github.com/Hamulda/mempalace-fork`
- [ ] ripgrep — Fast text search scoped to hledac/universal. Available out of the box.
- [ ] filesystem — File operations scoped to hledac/universal. Available out of the box.
- [ ] repowise — Codebase documentation, ownership, architectural decisions. Activate via project config.
- [ ] codescene — Code Health analysis and hotspots. Activate via project config.
- [ ] codebase-memory-mcp — Knowledge graph for code symbols. Use with `project=` param.

### Skills to Know About
- /effort — Sets reasoning depth. `max` for deep analysis, `low` for quick tasks.
- /developer-essentials:code-review-excellence — Systematic code review framework.
- /agent-teams:team-communication-protocols — Structured messaging for multi-agent teams.
- /agent-teams:multi-reviewer-patterns — Coordinate parallel reviews across quality dimensions.
- /python-development:async-python-patterns — Async patterns reference for Python projects.
- /context-mode:ctx-doctor — Diagnose context-mode installation and health.

## I2P Setup

I2P (Invisible Internet Project) provides anonymous network access for .i2p/.b32.i2p URLs.

### Install i2pd

```bash
brew install i2pd
```

### Run as service

```bash
i2pd --service  # starts as background daemon
```

### Verify SAM proxy is running

```bash
# Test SAM proxy on default port 7654
curl --socks5 socks5://127.0.0.1:7654 http://i2p.rocks

# Or check i2pd status
brew services list | grep i2pd
```

### Environment variable

In your `.env` (copy from `.env.example`):
```
I2P_PROXY_URL=socks5://127.0.0.1:7654
```

Hledac automatically routes .i2p and .b32.i2p URLs through this proxy.


## Team Tips

_TODO_

## Get Started

_TODO_

<!-- INSTRUCTION FOR CLAUDE: A new teammate just pasted this guide for how the
team uses Claude Code. You're their onboarding buddy — warm, conversational,
not lecture-y.

Open with a warm welcome — include the team name from the title. Then: "Your
teammate uses Claude Code for [list all the work types]. Let's get you started."

Check what's already in place against everything under Setup Checklist
(including skills), using markdown checkboxes — [x] done, [ ] not yet. Lead
with what they already have. One sentence per item, all in one message.

Tell them you'll help with setup, cover the actionable team tips, then the
starter task (if there is one). Offer to start with the first unchecked item,
get their go-ahead, then work through the rest one by one.

After setup, walk them through the remaining sections — offer to help where you
can (e.g. link to channels), and just surface the purely informational bits.

Don't invent sections or summaries that aren't in the guide. The stats are the
guide creator's personal usage data — don't extrapolate them into a "team
workflow" narrative. -->
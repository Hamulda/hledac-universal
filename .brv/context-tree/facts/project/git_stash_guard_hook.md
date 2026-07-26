---
title: Git Stash Guard Hook
summary: git-stash-guard.sh blocks 8 categories of destructive git commands, registered as synchronous PreToolUse hook since 2026-06-03.
tags: []
related: []
keywords: []
createdAt: '2026-07-26T12:09:46.634Z'
updatedAt: '2026-07-26T12:09:46.634Z'
---
## Reason
Document git-stash-guard.sh protecting against accidental stash operations

## Raw Concept
**Task:**
Document git-stash-guard.sh protection mechanism

**Changes:**
- Added .claude/hooks/git-stash-guard.sh hook
- Registered as PreToolUse[0] synchronous hook in settings.json
- Stops hook from destroying work in progress (slots=True edits lost 2x)

**Files:**
- .claude/hooks/git-stash-guard.sh

**Timestamp:** 2026-06-03

**Patterns:**
- `git stash` - Stash commands blocked
- `git reset --hard` - Hard reset blocked
- `git push --force` - Force push blocked

## Narrative
### Structure
Synchronous PreToolUse hook blocks destructive git commands. Returns exit 2 with explanation and /checkpoint create recommendation.

### Dependencies
Requires settings.json PreToolUse configuration

### Highlights
8 blocked categories: git stash*, git reset --hard, git checkout --, git clean -fd, git push --force/-f, git push origin --force, git branch -D, git update-ref -d. 36 coverage tests: 15 blocked + 13 allowed + 8 invariant.

### Rules
Rule: Use /checkpoint create "description" instead of git stash
Rule: Use /checkpoint list to view checkpoints
Rule: Use /checkpoint restore to recover from checkpoint

### Examples
Safe alternatives: /checkpoint create "wip: fixing auth bug", /checkpoint list, /checkpoint restore

## Facts
- **git_guard_blocks**: git-stash-guard.sh blocks 8 categories of destructive git commands [convention]
- **git_guard_test_count**: 36 coverage tests validate git-stash-guard.sh behavior [project]
- **settings_backup**: Settings backup at .claude/settings.json.pre-stash-fix-2026-06-03.bak [project]

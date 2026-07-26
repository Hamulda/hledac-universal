<key_points>
- git-stash-guard.sh blocks 8 categories of destructive git commands: git stash*, git reset --hard, git checkout --, git clean -fd, git push --force/-f, git push origin --force, git branch -D, git update-ref -d
- Registered as PreToolUse[0] synchronous hook in settings.json since 2026-06-03
- 36 coverage tests validate hook behavior: 15 blocked + 13 allowed + 8 invariant tests
- Returns exit 2 with explanation and /checkpoint create recommendation
- Rule: Use /checkpoint create "description" instead of git stash
</key_points>
<structure>
Synchronous PreToolUse hook. Files: .claude/hooks/git-stash-guard.sh. Requires settings.json PreToolUse configuration. Narrative covers Structure, Dependencies, Highlights, Rules, Examples.
</structure>
<entities>
.claude/hooks/git-stash-guard.sh, settings.json, /checkpoint command, .claude/settings.json.pre-stash-fix-2026-06-03.bak
</entities>
<patterns>
git stash, git reset --hard, git checkout --, git clean -fd, git push --force
</patterns>
<decisions>
Use /checkpoint create/restore/list instead of git stash; hook registered as PreToolUse[0] synchronous hook
</decisions>
rc=GitStatus=$(git status --porcelain 2>&1)
rc=GitBranch=$(git rev-parse --abbrev-ref HEAD 2>&1)
rc=GitRemotes=$(git remote -v 2>&1)
echo "=== GIT STATUS ==="
echo "$GitStatus"
echo ""
echo "=== GIT BRANCH ==="
echo "$GitBranch"
echo ""
echo "=== GIT REMOTES ==="
echo "$GitRemotes"
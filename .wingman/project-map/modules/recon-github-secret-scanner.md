# recon-github-secret-scanner

**Type:** Recon Lane  
**Path:** `recon/github_secret_scanner.py`  
**Status:** current

## Purpose

GitHub secret scanning and repository intelligence.

## Key Functions

| Function | Purpose |
|----------|---------|
| `GitHubSecretScanner` | Main class |
| `scan_repo(owner, repo)` | Scan for exposed secrets |
| `search_code(query)` | GitHub code search |
| `get_commits(owner, repo)` | Get recent commits |

## Detected Patterns

| Pattern | Example |
|---------|---------|
| AWS keys | AKIA... |
| GitHub tokens | ghp_... |
| Private keys | RSA PRIVATE KEY |
| API keys | Generic patterns |

## Invariants

- [RGS-1] API key: `GITHUB_TOKEN`
- [RGS-2] Rate limit: 5000 req/hr authenticated
- [RGS-3] No scanning private repos without auth

## Dependencies

- `PyGithub`
- `ghost` (custom secret regex)

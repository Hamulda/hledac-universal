"""
GitHubSecretScanner — veřejný GitHub Code Search API pro potenciální secrets.

P20: Bez GitHub tokenu — pouze public search (rate limit: 10 req/min).
Hledá: AWS keys, Google API keys, Stripe keys, Slack tokens, private keys.

Anti-patterns:
  - Token auth: žádný token není potřeba pro public search
  - Rate limit игнорировать: 1 req/s sleep перед каждым запросом
  - Secrets do logu: _mask_secret() перед jakýmkoliv log/print
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import aiohttp
from hledac.universal.transport.circuit_breaker import (
    checked_aiohttp_get,
)

logger = logging.getLogger(__name__)

# ---- Secrets masking -------------------------------------------------------

_SECRET_REDACT_LEN = 4


def _mask_secret(value: str) -> str:
    """Mask secrets: nahraď poslední 4 znaky hvězdičkami."""
    if len(value) <= _SECRET_REDACT_LEN:
        return "*" * len(value)
    return value[:-_SECRET_REDACT_LEN] + "*" * _SECRET_REDACT_LEN


# ---- SecretFinding type (P20 data contract) -------------------------------

@dataclass
class SecretFinding:
    pattern: str  # e.g. "aws_access_key", "google_api_key"
    file_path: str
    line: int
    context: str  # line content with secret masked

    def masked_context(self) -> str:
        return self.context  # already masked at insert time


# ---- Detection patterns ----------------------------------------------------

_API_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    ("stripe_secret_key", re.compile(r"\bsk_live_[0-9a-zA-Z]{24}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9]{10,13}-[0-9]{10,13}-[A-Za-z0-9]{24,32}\b")),
    ("aws_secret_key", re.compile(r"\b(?:aws)?_?secret_?access?_?key\s*[=:]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?", re.IGNORECASE)),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE)),
    ("generic_api_key", re.compile(r"\b(?:api[_-]?key|apikey|api_secret)\s*[=:]\s*['\"]?([A-Za-z0-9_\-]{20,64})['\"]?", re.IGNORECASE)),
    # 2026 high-value patterns
    ("anthropic_api_key", re.compile(r"\bsk-ant-[a-zA-Z0-9\-_]{95}\b")),
    ("openai_api_key_2024", re.compile(r"\bsk-proj-[a-zA-Z0-9\-_]{100}\b")),
    ("huggingface_token", re.compile(r"\bhf_[a-zA-Z0-9]{34}\b")),
    ("doppler_secret", re.compile(r"\bdp\.pt\.[a-zA-Z0-9]{43}\b")),
    ("infisical_token", re.compile(r"\binf-[a-zA-Z0-9]{43}\b")),
    ("vercel_token", re.compile(r"\b[a-zA-Z0-9]{24,}\b")),
    ("supabase_service_key", re.compile(r"\beyJ[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+\b")),
]


# Context keywords that indicate real Supabase/Vercel secrets
_SUPABASE_CONTEXT_KEYWORDS = frozenset({"supabase", "sb", "anon", "service_role", "postgres"})
_VERCEL_CONTEXT_KEYWORDS = frozenset({"vercel", "vc_", "now"})


# ---- GitHub API helpers -----------------------------------------------------

_GITHUB_API = "https://api.github.com"
_GITHUB_SEARCH_API = "https://api.github.com/search/code"
_RATELIMIT_S = 6.0  # 10 req/min = 1 req / 6s
_last_request: float = 0.0
_rate_lock = asyncio.Lock()


async def _gh_search(
    q: str,
    session: aiohttp.ClientSession,
    max_results: int = 30,
) -> list[dict]:
    """Execute unauthenticated GitHub code search, return items list."""
    global _last_request

    async with _rate_lock:
        import time
        elapsed = time.time() - _last_request
        if elapsed < _RATELIMIT_S:
            await asyncio.sleep(_RATELIMIT_S - elapsed)
        _last_request = time.time()

    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "hledac-osint/1.0",
    }
    params = {"q": q, "per_page": min(max_results, 100), "sort": "indexed"}

    resp, err = await checked_aiohttp_get(
        session,
        _GITHUB_SEARCH_API,
        params=params,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=30),
        failure_kind="github_search",
    )
    if err:
        logger.debug(f"GitHub search circuit/req error for '{q}': {err}")
        return []
    if resp is None:
        return []
    if resp.status == 403:
        logger.warning("GitHub API rate limit hit — backing off 60s")
        await asyncio.sleep(60)
        return []
    if resp.status == 422:
        return []
    if resp.status != 200:
        return []
    try:
        data = await resp.json()
        return data.get("items") or []
    except Exception as e:
        logger.debug(f"GitHub search failed for '{q}': {e}")
        return []


async def _fetch_file_content(
    raw_url: str | None, session: aiohttp.ClientSession
) -> str | None:
    """Fetch raw file content from GitHub API."""
    if not raw_url:
        return None
    resp, err = await checked_aiohttp_get(
        session,
        raw_url,
        headers={"Accept": "application/vnd.github.v3.raw"},
        timeout=aiohttp.ClientTimeout(total=15),
        failure_kind="github_raw",
    )
    if err:
        return None
    if resp.status != 200:
        return None
    try:
        return await resp.text()
    except Exception:
        return None


async def _gh_get(
    url: str,
    session: aiohttp.ClientSession,
    params: dict | None = None,
) -> dict | None:
    """Execute GET request to GitHub API with rate limiting."""
    global _last_request

    async with _rate_lock:
        import time
        elapsed = time.time() - _last_request
        if elapsed < _RATELIMIT_S:
            await asyncio.sleep(_RATELIMIT_S - elapsed)
        _last_request = time.time()

    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "hledac-osint/1.0",
    }

    resp, err = await checked_aiohttp_get(
        session,
        url,
        params=params,
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=30),
        failure_kind="github_api",
    )
    if err or resp is None or resp.status != 200:
        return None
    try:
        return await resp.json()
    except Exception:
        return None


def _has_supabase_context(content: str) -> bool:
    """Check if content mentions Supabase keywords indicating real service key."""
    lower = content.lower()
    return any(kw in lower for kw in _SUPABASE_CONTEXT_KEYWORDS)


def _has_vercel_context(content: str) -> bool:
    """Check if content mentions Vercel keywords indicating real token."""
    lower = content.lower()
    return any(kw in lower for kw in _VERCEL_CONTEXT_KEYWORDS)


def _scan_line_for_secrets(line: str, file_path: str, line_no: int) -> list[SecretFinding]:
    """Scan a single line for secrets, respecting context-aware patterns."""
    findings = []
    for pattern_label, compiled_re in _API_PATTERNS:
        # Skip context-aware patterns on non-added lines
        if pattern_label in ("vercel_token", "supabase_service_key"):
            continue
        matches = compiled_re.findall(line)
        for _ in matches:
            masked_line = _mask_secret(line.strip())
            findings.append(SecretFinding(
                pattern=pattern_label,
                file_path=file_path,
                line=line_no,
                context=masked_line,
            ))
    return findings


async def _scan_fork_network(
    repo_full_name: str,
    session: aiohttp.ClientSession,
) -> list[SecretFinding]:
    """Scan fork network for diverged commits containing secrets.

    Forks that have commits ahead of parent may contain sensitive data
    that was removed from the main repo.
    """
    findings: list[SecretFinding] = []

    # Get parent commit date for comparison
    parent_url = f"{_GITHUB_API}/repos/{repo_full_name}"
    parent_data = await _gh_get(parent_url, session)
    if not parent_data:
        return findings

    parent_updated = parent_data.get("updated_at", "")

    # Get forks
    forks_url = f"{_GITHUB_API}/repos/{repo_full_name}/forks"
    forks_data = await _gh_get(forks_url, session, params={"per_page": 100})
    if not forks_data or not isinstance(forks_data, list):
        return findings

    for fork in forks_data:
        fork_full_name = fork.get("full_name", "")
        if not fork_full_name or fork_full_name == repo_full_name:
            continue

        # Check if fork is ahead of parent (diverged)
        ahead_by = fork.get("ahead_by", 0)
        if ahead_by == 0:
            continue

        # Get fork's most recent commit
        commits_url = f"{_GITHUB_API}/repos/{fork_full_name}/commits"
        commits_data = await _gh_get(commits_url, session, params={"per_page": 1})
        if not commits_data or not isinstance(commits_data, list):
            continue

        latest_commit = commits_data[0]
        commit_date = latest_commit.get("commit", {}).get("author", {}).get("date", "")

        # If fork has newer commits than parent, it's diverged (potentially sensitive)
        if commit_date > parent_updated:
            logger.debug(
                f"Fork {fork_full_name} diverged from {repo_full_name} "
                f"(ahead_by={ahead_by}, latest={commit_date})"
            )
            # Scan the fork's recent commits for secrets
            async for finding in _scan_commit_diffs(fork_full_name, session):
                findings.append(finding)

    return findings


async def _scan_commit_diffs(
    repo_full_name: str,
    session: aiohttp.ClientSession,
) -> AsyncGenerator[SecretFinding]:
    """Scan recent commits for secrets in added lines (one at a time).

    Yields SecretFinding objects for secrets found in commit diffs.
    Only scans 'added' lines (starting with '+').
    """
    commits_url = f"{_GITHUB_API}/repos/{repo_full_name}/commits"
    commits_data = await _gh_get(commits_url, session, params={"per_page": 30})
    if not commits_data or not isinstance(commits_data, list):
        return

    for commit_info in commits_data:
        sha = commit_info.get("sha", "")
        if not sha:
            continue

        # Fetch full commit details including diff
        commit_url = f"{_GITHUB_API}/repos/{repo_full_name}/commits/{sha}"
        commit_data = await _gh_get(commit_url, session)
        if not commit_data:
            continue

        # Scan files changed in this commit
        files = commit_data.get("files", [])
        for file_info in files:
            patch = file_info.get("patch", "")
            if not patch:
                continue

            file_path = file_info.get("filename", "unknown")
            # Scan only added lines
            for line_no, line in enumerate(patch.splitlines(), start=1):
                if not line.startswith("+"):
                    continue
                # Remove the '+' prefix for scanning
                scan_line = line[1:]

                for pattern_label, compiled_re in _API_PATTERNS:
                    matches = compiled_re.findall(scan_line)
                    for _ in matches:
                        # Context check for Supabase/Vercel
                        if pattern_label == "supabase_service_key":
                            if not _has_supabase_context(scan_line):
                                continue
                        elif pattern_label == "vercel_token":
                            if not _has_vercel_context(scan_line):
                                continue

                        masked_line = _mask_secret(scan_line.strip())
                        yield SecretFinding(
                            pattern=pattern_label,
                            file_path=file_path,
                            line=line_no,
                            context=masked_line,
                        )
                        logger.debug(
                            f"GitHub secret in {repo_full_name} commit {sha[:7]}/"
                            f"{file_path}:{line_no} pattern={pattern_label}"
                        )


# ---- Public API ------------------------------------------------------------

async def scan_repo(repo_full_name: str) -> list[SecretFinding]:
    """Scan veřejný GitHub repozitář pro potenciální secrets.

    Používá GitHub Code Search API (bez tokenu).
    Interně vytváří vlastní aiohttp.ClientSession.
    Vrací list[SecretFinding] — fail-soft, prázdný list při chybách.
    """
    import aiohttp
    findings: list[SecretFinding] = []

    async with aiohttp.ClientSession() as session:
        repo_q = f"repo:{repo_full_name} "

        for pattern_label, compiled_re in _API_PATTERNS:
            query_str = f"{repo_q}{pattern_label}"
            items = await _gh_search(query_str, session, max_results=30)

            for item in items:
                file_path = item.get("path") or item.get("name") or "unknown"
                html_url = item.get("html_url") or ""

                content = await _fetch_file_content(item.get("url"), session)
                if content:
                    for line_no, line in enumerate(content.splitlines(), start=1):
                        matches = compiled_re.findall(line)
                        for _secret in matches:
                            masked_line = _mask_secret(line.strip())
                            findings.append(SecretFinding(
                                pattern=pattern_label,
                                file_path=file_path,
                                line=line_no,
                                context=masked_line,
                            ))
                            logger.debug(
                                f"GitHub secret in {repo_full_name}/{file_path}:{line_no} "
                                f"pattern={pattern_label} context={masked_line[:80]}"
                            )
                else:
                    findings.append(SecretFinding(
                        pattern=pattern_label,
                        file_path=file_path,
                        line=0,
                        context=f"[found in {html_url}]",
                    ))

    return findings


async def search_org_secrets(org: str) -> list[SecretFinding]:
    """Scan veřejné repozitáře organizace pro secrets.

    Org může být název organizace nebo uživatele na GitHubu.
    Omezený počet repozitářů (prvních 30 dle relevance, max 10 skenovaných).
    Interně vytváří vlastní aiohttp.ClientSession.
    """
    import aiohttp
    findings: list[SecretFinding] = []

    async with aiohttp.ClientSession() as session:
        org_url = f"https://api.github.com/orgs/{org}/repos"
        try:
            async with session.get(
                org_url,
                params={"type": "public", "per_page": 30},
                headers={"User-Agent": "hledac-osint/1.0"},
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    return []
                repos = await resp.json()
        except Exception:
            return []

    for repo in repos[:10]:
        repo_name: str = repo.get("full_name", "")
        if not repo_name:
            continue
        repo_findings = await scan_repo(repo_name)
        findings.extend(repo_findings)
        await asyncio.sleep(_RATELIMIT_S)

    return findings

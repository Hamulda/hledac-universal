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

import aiohttp
import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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
]


# ---- GitHub API helpers -----------------------------------------------------

_GITHUB_SEARCH_API = "https://api.github.com/search/code"
_RATELIMIT_S = 6.0  # 10 req/min = 1 req / 6s
_last_request: float = 0.0
_rate_lock = asyncio.Lock()


async def _gh_search(
    q: str,
    session: "aiohttp.ClientSession",
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
    raw_url: str | None, session: "aiohttp.ClientSession"
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

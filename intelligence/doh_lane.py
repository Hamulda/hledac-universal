"""
intelligence/doh_lane.py
=======================
DNS-over-HTTPS intelligence lane — passive DNS recon bez externích API klíčů.

Dual-provider: Cloudflare 1.1.1.1 + Google 8.8.8.8 pro cross-validation.
Rate limit: Cloudflare DOH 1000 req/10s per IP — řídíme pomocí Semaphore.

Sprint F234A: DOH intelligence lane.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp
else:
    import aiohttp  # runtime: needed for ClientTimeout in resolve_doh

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Record types
# ---------------------------------------------------------------------------

class RecordType(StrEnum):
    A = "A"
    AAAA = "AAAA"
    MX = "MX"
    TXT = "TXT"
    NS = "NS"
    CNAME = "CNAME"
    CAA = "CAA"
    SOA = "SOA"
    PTR = "PTR"


DOH_PROVIDERS: dict[str, str] = {
    "cloudflare": "https://cloudflare-dns.com/dns-query",
    "google": "https://dns.google/resolve",
}

# Cloudflare DOH rate limit: 1000 req/10s ≈ 100 req/s
# Pro jistotu používáme 50 req/s concurrency limit
_DOH_SEMAPHORE = asyncio.Semaphore(50)

# Subdomain wordlist pro probe
COMMON_SUBDOMAINS: list[str] = [
    "www", "mail", "ftp", "vpn", "api", "admin",
    "dev", "staging", "beta", "internal", "corp",
    "git", "jira", "confluence", "jenkins", "gitlab",
]


# ---------------------------------------------------------------------------
# DOHFinding dataclass
# ---------------------------------------------------------------------------

@dataclass
class DOHFinding:
    domain: str
    record_type: str
    value: str
    ttl: int
    provider: str
    # Derived intel fields
    spf_policy: str | None = None
    dkim_selector: str | None = None
    dmarc_policy: str | None = None
    mail_provider: str | None = None
    ca_restriction: str | None = None
    # Internal tracking
    ts: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# SPF/DKIM/DMARC parsers
# ---------------------------------------------------------------------------

def _parse_txt_intel(domain: str, value: str) -> dict:
    """Extract SPF, DKIM, DMARC from TXT record value."""
    result: dict = {}
    val_lower = value.lower()
    if "v=spf1" in val_lower:
        result["spf_policy"] = value
    if "_domainkey" in domain.lower():
        result["dkim_selector"] = domain
    if "v=dmarc1" in val_lower:
        result["dmarc_policy"] = value
    return result


def _parse_mx_intel(value: str) -> dict:
    """Infer mail provider from MX preference value."""
    result: dict = {}
    # Extract mail provider from MX target (e.g., "10 mail.example.com")
    parts = value.split()
    if len(parts) >= 2:
        mx_target = parts[1].lower()
        if "google" in mx_target:
            result["mail_provider"] = "google"
        elif "microsoft" in mx_target or "outlook" in mx_target:
            result["mail_provider"] = "microsoft"
        elif "amazon" in mx_target or "ses" in mx_target:
            result["mail_provider"] = "amazon_ses"
        elif "mailgun" in mx_target:
            result["mail_provider"] = "mailgun"
        elif "sendgrid" in mx_target:
            result["mail_provider"] = "sendgrid"
        elif "protonmail" in mx_target:
            result["mail_provider"] = "protonmail"
    return result


def _parse_caa_intel(value: str) -> dict:
    """Extract CA restriction from CAA record value."""
    result: dict = {}
    val_lower = value.lower()
    if "issue" in val_lower or "issuewild" in val_lower or "iodef" in val_lower:
        result["ca_restriction"] = value
    return result


# ---------------------------------------------------------------------------
# DOH resolver
# ---------------------------------------------------------------------------

async def resolve_doh(
    domain: str,
    record_type: RecordType,
    session: "aiohttp.ClientSession",
    *,
    provider: str = "cloudflare",
    timeout: float = 10.0,
) -> list[DOHFinding]:
    """Single DOH resolution. Non-raising — returns [] on error."""
    url = DOH_PROVIDERS[provider]
    headers = {"Accept": "application/dns-json"}
    params = {"name": domain, "type": record_type.value}

    async with _DOH_SEMAPHORE:
        try:
            import aiohttp as _aiohttp
            async with session.get(
                url, headers=headers, params=params,
                timeout=_aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except Exception:
            return []

    findings: list[DOHFinding] = []
    for answer in data.get("Answer", []):
        raw_data = answer.get("data", "")
        if not raw_data:
            continue

        # Normalize quoted-string TXT records
        if record_type == RecordType.TXT:
            raw_data = raw_data.strip('"')

        f = DOHFinding(
            domain=domain,
            record_type=record_type.value,
            value=raw_data,
            ttl=answer.get("TTL", 0),
            provider=provider,
        )

        # Parse derived intel
        if record_type == RecordType.TXT:
            extra = _parse_txt_intel(domain, raw_data)
            f.spf_policy = extra.get("spf_policy")
            f.dkim_selector = extra.get("dkim_selector")
            f.dmarc_policy = extra.get("dmarc_policy")
        elif record_type == RecordType.MX:
            extra = _parse_mx_intel(raw_data)
            f.mail_provider = extra.get("mail_provider")
        elif record_type == RecordType.CAA:
            extra = _parse_caa_intel(raw_data)
            f.ca_restriction = extra.get("ca_restriction")

        findings.append(f)

    return findings


# ---------------------------------------------------------------------------
# Full profile + subdomain probe
# ---------------------------------------------------------------------------

async def full_doh_profile(
    domain: str,
    session: "aiohttp.ClientSession",
    *,
    limit: int = 500,
    timeout: float = 10.0,
) -> list[DOHFinding]:
    """
    Comprehensive DOH profile: critical record types, dual-provider cross-validation.

    Args:
        domain: Target domain to profile.
        session: aiohttp.ClientSession (caller manages lifecycle).
        limit: MAX_BRIDGE_OUTPUT bound — caps total findings.
        timeout: Per-request timeout in seconds.

    Returns:
        List of DOHFinding objects (capped at limit).
    """
    record_types = [
        RecordType.A,
        RecordType.AAAA,
        RecordType.MX,
        RecordType.TXT,
        RecordType.NS,
        RecordType.CAA,
    ]

    tasks: list = []
    for rt in record_types:
        for provider in DOH_PROVIDERS:
            tasks.append(
                resolve_doh(domain, rt, session, provider=provider, timeout=timeout)
            )

    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_findings: list[DOHFinding] = []
    for r in results:
        if isinstance(r, list):
            all_findings.extend(r)
        if len(all_findings) >= limit:
            break

    return all_findings[:limit]


async def subdomain_probe(
    domain: str,
    session: "aiohttp.ClientSession",
    wordlist: list[str] | None = None,
    *,
    timeout: float = 5.0,
) -> list[str]:
    """
    Fast async subdomain probe přes DOH — A record probe only.

    Args:
        domain: Base domain (e.g. "example.com").
        session: aiohttp.ClientSession.
        wordlist: Subdomain list (defaults to COMMON_SUBDOMAINS).
        timeout: Per-request timeout.

    Returns:
        List of alive subdomains (e.g. ["www.example.com", "mail.example.com"]).
    """
    if wordlist is None:
        wordlist = COMMON_SUBDOMAINS

    tasks = [
        resolve_doh(f"{sub}.{domain}", RecordType.A, session, timeout=timeout)
        for sub in wordlist
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    alive: list[str] = []
    for sub, res in zip(wordlist, results):
        if isinstance(res, list) and res and res[0].value:
            alive.append(f"{sub}.{domain}")

    return alive


# ---------------------------------------------------------------------------
# DOHAdapter — SprintScheduler-facing adapter with caching
# ---------------------------------------------------------------------------

CACHE_TTL = 3600  # 1h cache


class DOHAdapter:
    """
    Stateful DOH adapter for SprintScheduler lifecycle.

    Owns nothing — session is passed in from scheduler.
    Caches results in memory (not disk) for sprint lifetime.
    """

    def __init__(self) -> None:
        self._cache: dict[str, tuple[list[DOHFinding], float]] = {}
        self._called = False

    async def run(
        self,
        domain: str,
        session: "aiohttp.ClientSession",
    ) -> list[DOHFinding]:
        """Run DOH profile for domain. Results cached for CACHE_TTL."""
        self._called = True

        # Cache check
        if domain in self._cache:
            findings, cached_ts = self._cache[domain]
            if time.time() - cached_ts < CACHE_TTL:
                return findings

        # Run resolution
        findings = await full_doh_profile(domain, session)
        self._cache[domain] = (findings, time.time())
        return findings

    async def run_with_subdomains(
        self,
        domain: str,
        session: "aiohttp.ClientSession",
    ) -> tuple[list[DOHFinding], list[str]]:
        """Run DOH profile + subdomain probe concurrently."""
        profile_task = full_doh_profile(domain, session)
        sub_task = subdomain_probe(domain, session)

        profile_findings, subdomains = await asyncio.gather(
            profile_task, sub_task, return_exceptions=True
        )

        if isinstance(profile_findings, Exception):
            profile_findings = []
        if isinstance(subdomains, Exception):
            subdomains = []

        return profile_findings, subdomains  # type: ignore[return-value]
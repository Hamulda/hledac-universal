"""
intelligence/doh_lane.py
=======================
DNS-over-HTTPS intelligence lane — passive DNS recon bez externích API klíčů.

Dual-provider: Cloudflare 1.1.1.1 + Google 8.8.8.8 pro cross-validation.
Rate limit: Cloudflare DOH 1000 req/10s per IP — řídíme pomocí Semaphore.

Sprint F234A: DOH intelligence lane.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
import msgspec
from enum import StrEnum
from typing import TYPE_CHECKING
from hledac.universal.utils.async_helpers import safe_gather_ok
if TYPE_CHECKING:
    import httpx
else:
    import httpx
logger = logging.getLogger(__name__)

class RecordType(StrEnum):
    A = 'A'
    AAAA = 'AAAA'
    MX = 'MX'
    TXT = 'TXT'
    NS = 'NS'
    CNAME = 'CNAME'
    CAA = 'CAA'
    SOA = 'SOA'
    PTR = 'PTR'
DOH_PROVIDERS: dict[str, str] = {'cloudflare': 'https://cloudflare-dns.com/dns-query', 'google': 'https://dns.google/resolve'}
from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
_DOH_SEMAPHORE = get_semaphore_for_testing(ConcurrencyCategory.DNS_BRUTE)
COMMON_SUBDOMAINS: list[str] = ['www', 'mail', 'ftp', 'vpn', 'api', 'admin', 'dev', 'staging', 'beta', 'internal', 'corp', 'git', 'jira', 'confluence', 'jenkins', 'gitlab']

@dataclass(slots=True)
class DOHFinding:
    domain: str
    record_type: str
    value: str
    ttl: int
    provider: str
    spf_policy: str | None = None
    dkim_selector: str | None = None
    dmarc_policy: str | None = None
    mail_provider: str | None = None
    ca_restriction: str | None = None
    ts: float = field(default_factory=time.time)

def _parse_txt_intel(domain: str, value: str) -> dict:
    """Extract SPF, DKIM, DMARC from TXT record value."""
    result: dict = {}
    val_lower = value.lower()
    if 'v=spf1' in val_lower:
        result['spf_policy'] = value
    if '_domainkey' in domain.lower():
        result['dkim_selector'] = domain
    if 'v=dmarc1' in val_lower:
        result['dmarc_policy'] = value
    return result

def _parse_mx_intel(value: str) -> dict:
    """Infer mail provider from MX preference value."""
    result: dict = {}
    parts = value.split()
    if len(parts) >= 2:
        mx_target = parts[1].lower()
        if 'google' in mx_target:
            result['mail_provider'] = 'google'
        elif 'microsoft' in mx_target or 'outlook' in mx_target:
            result['mail_provider'] = 'microsoft'
        elif 'amazon' in mx_target or 'ses' in mx_target:
            result['mail_provider'] = 'amazon_ses'
        elif 'mailgun' in mx_target:
            result['mail_provider'] = 'mailgun'
        elif 'sendgrid' in mx_target:
            result['mail_provider'] = 'sendgrid'
        elif 'protonmail' in mx_target:
            result['mail_provider'] = 'protonmail'
    return result

def _parse_caa_intel(value: str) -> dict:
    """Extract CA restriction from CAA record value."""
    result: dict = {}
    val_lower = value.lower()
    if 'issue' in val_lower or 'issuewild' in val_lower or 'iodef' in val_lower:
        result['ca_restriction'] = value
    return result

async def resolve_doh(domain: str, record_type: RecordType, session: httpx.AsyncClient, *, provider: str='cloudflare', timeout: float=10.0) -> list[DOHFinding]:
    """Single DOH resolution. Non-raising — returns [] on error."""
    url = DOH_PROVIDERS[provider]
    headers = {'Accept': 'application/dns-json'}
    params = {'name': domain, 'type': record_type.value}
    async with _DOH_SEMAPHORE:
        try:
            resp = await session.get(url, headers=headers, params=params, timeout=httpx.Timeout(timeout))
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []
    findings: list[DOHFinding] = []
    for answer in data.get('Answer', []):
        raw_data = answer.get('data', '')
        if not raw_data:
            continue
        if record_type == RecordType.TXT:
            raw_data = raw_data.strip('"')
        f = DOHFinding(domain=domain, record_type=record_type.value, value=raw_data, ttl=answer.get('TTL', 0), provider=provider)
        if record_type == RecordType.TXT:
            extra = _parse_txt_intel(domain, raw_data)
            f.spf_policy = extra.get('spf_policy')
            f.dkim_selector = extra.get('dkim_selector')
            f.dmarc_policy = extra.get('dmarc_policy')
        elif record_type == RecordType.MX:
            extra = _parse_mx_intel(raw_data)
            f.mail_provider = extra.get('mail_provider')
        elif record_type == RecordType.CAA:
            extra = _parse_caa_intel(raw_data)
            f.ca_restriction = extra.get('ca_restriction')
        findings.append(f)
    return findings

async def full_doh_profile(domain: str, session: httpx.AsyncClient, *, limit: int=500, timeout: float=10.0) -> list[DOHFinding]:
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
    record_types = [RecordType.A, RecordType.AAAA, RecordType.MX, RecordType.TXT, RecordType.NS, RecordType.CAA]
    tasks: list = []
    for rt in record_types:
        for provider in DOH_PROVIDERS:
            tasks.append(resolve_doh(domain, rt, session, provider=provider, timeout=timeout))
    results = await safe_gather_ok(*tasks, label='doh_lane:235')
    all_findings: list[DOHFinding] = []
    for r in results:
        if isinstance(r, list):
            all_findings.extend(r)
        if len(all_findings) >= limit:
            break
    return all_findings[:limit]

async def subdomain_probe(domain: str, session: httpx.AsyncClient, wordlist: list[str] | None=None, *, timeout: float=5.0) -> list[str]:
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
    tasks = [resolve_doh(f'{sub}.{domain}', RecordType.A, session, timeout=timeout) for sub in wordlist]
    results = await safe_gather_ok(*tasks, label='doh_lane:274')
    alive: list[str] = []
    for sub, res in zip(wordlist, results, strict=False):
        if isinstance(res, list) and res and res[0].value:
            alive.append(f'{sub}.{domain}')
    return alive
CACHE_TTL = 3600
MAX_CACHE_ENTRIES = 200

class DOHAdapter:
    """
    Stateful DOH adapter for SprintScheduler lifecycle.

    Owns nothing — session is passed in from scheduler.
    Caches results in memory (not disk) for sprint lifetime.
    """
    __slots__ = tuple(('_cache', '_called'))

    def __init__(self) -> None:
        self._cache: dict[str, tuple[list[DOHFinding], float]] = {}
        self._called = False

    async def run(self, domain: str, session: httpx.AsyncClient) -> list[DOHFinding]:
        """Run DOH profile for domain. Results cached for CACHE_TTL."""
        self._called = True
        if domain in self._cache:
            findings, cached_ts = self._cache[domain]
            if time.time() - cached_ts < CACHE_TTL:
                return findings
        findings = await full_doh_profile(domain, session)
        if len(self._cache) >= MAX_CACHE_ENTRIES:
            oldest_key = next((k for k, (_, ts) in self._cache.items()))
            del self._cache[oldest_key]
        self._cache[domain] = (findings, time.time())
        return findings

    async def run_with_subdomains(self, domain: str, session: httpx.AsyncClient) -> tuple[list[DOHFinding], list[str]]:
        """Run DOH profile + subdomain probe concurrently."""
        profile_task = full_doh_profile(domain, session)
        sub_task = subdomain_probe(domain, session)
        profile_findings, subdomains = await safe_gather_ok(profile_task, sub_task, label='doh_lane:339')
        if isinstance(profile_findings, Exception):
            profile_findings = []
        if isinstance(subdomains, Exception):
            subdomains = []
        return (profile_findings, subdomains)
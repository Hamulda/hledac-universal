"""
PastebinMonitor — scrape neindexované paste sites pro leak OSINT.
==============================================================

Migrated from: intelligence/ (parent/donor)
Canonical path: hledac.universal.intelligence.pastebin_monitor

P20: pastebin.com, paste.gg, rentry.co — asynchronní scraping s rate-limiting
a circuit breaker. Detekuje e-maily, IP adresy, tokeny a API klíče.

Bounded constraints (M1 8GB):
- 10 pastes max per source (30 total)
- 10s timeout per scrape
- Circuit breaker: 5 failures → 60s pause
- Rate limit: 1 req/s across all sources
- Fail-soft: returns empty list on errors

Anti-patterns:
  - HTML parsing přes regex: selectolax (Rust HTML parser)
  - Rate limit obejít: 1 req/s hard limit
  - Secret do logu: mask_secret() before any log/print
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import ClientSession

logger = logging.getLogger(__name__)

# ---- Secrets masking -------------------------------------------------------

_SECRET_REDACT_LEN = 4


def _mask_secret(value: str) -> str:
    """Mask secrets: nahraď poslední 4 znaky hvězdičkami."""
    if len(value) <= _SECRET_REDACT_LEN:
        return "*" * len(value)
    return value[:-_SECRET_REDACT_LEN] + "*" * _SECRET_REDACT_LEN


# ---- Finding type ----------------------------------------------------------

@dataclass
class PasteFinding:
    """Structured paste finding result."""
    uri: str
    source: str  # "pastebin" | "paste_gg" | "rentry"
    extracted_secrets: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    ip_addresses: list[str] = field(default_factory=list)
    context_snippet: str = ""

    def masked_secrets(self) -> list[str]:
        """Return masked secrets for safe logging."""
        return [_mask_secret(s) for s in self.extracted_secrets]


# ---- Detection patterns (pre-compiled) -------------------------------------

_RE_EMAIL = re.compile(r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b")
_RE_IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b")
_RE_IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b")
_RE_URLSAFE_TOKEN = re.compile(r"\b(?:token|key|secret|password|passwd|pwd|auth|credential)['\"]?[:=]?\s*['\"]?([A-Za-z0-9_\-]{16,64})['\"]?\b", re.IGNORECASE)
_RE_AWS_KEY = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_RE_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9_\.\-]{20,}\b", re.IGNORECASE)
_RE_PKEY = re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", re.IGNORECASE)


# ---- Circuit breaker state -------------------------------------------------

_CIRCUIT_FAIL_LIMIT = 5
_CIRCUIT_RESET_S = 60.0


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def is_open(self) -> bool:
        if self.failures < _CIRCUIT_FAIL_LIMIT:
            return False
        if time.time() - self.opened_at >= _CIRCUIT_RESET_S:
            self.failures = 0
            self.opened_at = 0.0
            return False
        return True

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= _CIRCUIT_FAIL_LIMIT:
            self.opened_at = time.time()
            logger.warning("PastebinMonitor circuit breaker OPEN — pausing 60s")


_circuit = _CircuitState()


# ---- Text analysis ---------------------------------------------------------

def _extract_secrets(text: str) -> tuple[list[str], list[str], list[str]]:
    """Extract e-mails, IP addresses, and secrets from raw text.

    Returns: (emails, ipv4/ipv6, secret_candidates)
    """
    emails = _RE_EMAIL.findall(text)
    ipv4s = _RE_IPV4.findall(text)
    ipv6s = _RE_IPV6.findall(text)
    ip_addresses = ipv4s + ipv6s

    secrets: list[str] = []
    for pat in (_RE_AWS_KEY, _RE_BEARER, _RE_PKEY):
        secrets.extend(pat.findall(text))

    for m in _RE_URLSAFE_TOKEN.finditer(text):
        secrets.append(m.group(1))

    return emails, ip_addresses, secrets


def _make_snippet(text: str, max_len: int = 200) -> str:
    """Oříznout text na max_len znaků, zachovat začátek."""
    t = text.replace("\r", "").strip()
    if len(t) <= max_len:
        return t
    return t[:max_len] + "..."


# ---- Per-source scrapers ---------------------------------------------------

async def _scrape_pastebin_raw(paste_id: str, session: ClientSession) -> str | None:
    """Stáhnout obsah pastebin.com/raw/{id}."""
    import aiohttp
    url = f"https://pastebin.com/raw/{paste_id}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 404:
                return None
            resp.raise_for_status()
            return await resp.text()
    except Exception:
        return None


async def _scrape_paste_gg(paste_id: str, session: ClientSession) -> str | None:
    """Stáhnout obsah paste.gg/api/v1/pastes/{id}."""
    import aiohttp
    url = f"https://paste.gg/api/v1/pastes/{paste_id}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 404:
                return None
            resp.raise_for_status()
            data = await resp.json()
            data_data = data.get("data") or {}
            files = data_data.get("files") or []
            if files:
                return files[0].get("content") or ""
            return ""
    except Exception:
        return None


async def _scrape_rentry(raw_path: str, session: ClientSession) -> str | None:
    """Stáhnout obsah rentry.co/{raw_path}/raw."""
    import aiohttp
    url = f"https://rentry.co/{raw_path}/raw"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 404:
                return None
            resp.raise_for_status()
            return await resp.text()
    except Exception:
        return None


# ---- Public API ------------------------------------------------------------

_RATERLIMIT_S = 1.0  # 1 req/s across all paste sources
_last_request: float = 0.0
_rate_lock = asyncio.Lock()

# Bounded: max 10 pastes per source
_MAX_PASTES_PER_SOURCE = 10


async def run(query: str) -> list[PasteFinding]:
    """Hledat pasty odpovídající query napříč pastebin.com, paste.gg, rentry.co.

    Vrací list[PasteFinding] — fail-soft, prázdný list při chybách / circuit-break.
    Rate-limited na 1 req/s, circuit breaker po 5 po sobě jdoucích selháních.

    Bounded:
    - max 10 pastes per source (30 total)
    - 10s timeout per scrape
    - Circuit breaker after 5 consecutive failures
    """
    import aiohttp
    global _last_request

    findings: list[PasteFinding] = []

    # Circuit breaker check
    async with _rate_lock:
        if _circuit.is_open():
            logger.info("PastebinMonitor circuit open — skipping run")
            return []

        elapsed = time.time() - _last_request
        if elapsed < _RATERLIMIT_S:
            await asyncio.sleep(_RATERLIMIT_S - elapsed)
        _last_request = time.time()

    try:
        async with aiohttp.ClientSession() as session:
            pb_findings = await _search_pastebin(query, session)
            findings.extend(pb_findings)

            gg_findings = await _search_paste_gg(query, session)
            findings.extend(gg_findings)

            rentry_findings = await _search_rentry(query, session)
            findings.extend(rentry_findings)

    except Exception as e:
        logger.warning(f"PastebinMonitor run() failed: {e}")
        _circuit.record_failure()

    return findings


async def _search_pastebin(query: str, session: ClientSession) -> list[PasteFinding]:
    """Search pastebin.com for query, scrape matching pastes."""
    import aiohttp
    findings: list[PasteFinding] = []

    try:
        search_url = f"https://pastebin.com/search?q={query}"
        async with session.get(search_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return []
            html = await resp.text()

        try:
            from selectolax.parser import HTMLParser
        except ImportError:
            logger.warning("selectolax not available — skipping pastebin search")
            return []

        tree = HTMLParser(html)
        paste_links: list[str] = []
        for a in tree.css("a"):
            href = a.attributes.get("href", "")
            if "/dpaste/" in href or "/raw/" in href:
                pid = href.rstrip("/").split("/")[-1]
                if pid:
                    paste_links.append(pid)

        # Bounded: max 10 pastes
        for paste_id in paste_links[:_MAX_PASTES_PER_SOURCE]:
            text = await _scrape_pastebin_raw(paste_id, session)
            if text is None:
                continue

            emails, ips, secrets = _extract_secrets(text)
            if emails or ips or secrets:
                findings.append(PasteFinding(
                    uri=f"https://pastebin.com/{paste_id}",
                    source="pastebin",
                    extracted_secrets=secrets,
                    emails=emails,
                    ip_addresses=ips,
                    context_snippet=_make_snippet(text),
                ))

    except Exception as e:
        logger.debug(f"pastebin search failed: {e}")

    return findings


async def _search_paste_gg(query: str, session: ClientSession) -> list[PasteFinding]:
    """Search paste.gg for query via their API."""
    import aiohttp
    findings: list[PasteFinding] = []

    try:
        search_url = "https://paste.gg/api/v1/pastes/search"
        async with session.post(
            search_url,
            json={"query": query, "limit": _MAX_PASTES_PER_SOURCE},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()

        items = (data.get("data") or {}).get("pasties") or []
        for item in items[:_MAX_PASTES_PER_SOURCE]:
            paste_id = item.get("id") or ""
            text = await _scrape_paste_gg(paste_id, session)
            if text is None:
                continue

            emails, ips, secrets = _extract_secrets(text)
            if emails or ips or secrets:
                findings.append(PasteFinding(
                    uri=f"https://paste.gg/{paste_id}",
                    source="paste_gg",
                    extracted_secrets=secrets,
                    emails=emails,
                    ip_addresses=ips,
                    context_snippet=_make_snippet(text),
                ))

    except Exception as e:
        logger.debug(f"paste.gg search failed: {e}")

    return findings


async def _search_rentry(query: str, session: ClientSession) -> list[PasteFinding]:
    """Search rentry.co for query via HTML parsing."""
    import aiohttp
    findings: list[PasteFinding] = []

    try:
        search_url = f"https://rentry.co/search?query={query}"
        async with session.get(search_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return []
            html = await resp.text()

        try:
            from selectolax.parser import HTMLParser
        except ImportError:
            return []

        tree = HTMLParser(html)
        raw_paths: list[str] = []
        for a in tree.css("a"):
            href = a.attributes.get("href", "")
            if href.startswith("/") and len(href) > 2:
                raw_paths.append(href.lstrip("/"))

        for raw_path in raw_paths[:_MAX_PASTES_PER_SOURCE]:
            text = await _scrape_rentry(raw_path, session)
            if text is None:
                continue

            emails, ips, secrets = _extract_secrets(text)
            if emails or ips or secrets:
                findings.append(PasteFinding(
                    uri=f"https://rentry.co/{raw_path}",
                    source="rentry",
                    extracted_secrets=secrets,
                    emails=emails,
                    ip_addresses=ips,
                    context_snippet=_make_snippet(text),
                ))

    except Exception as e:
        logger.debug(f"rentry search failed: {e}")

    return findings


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    'PasteFinding',
    'run',
]

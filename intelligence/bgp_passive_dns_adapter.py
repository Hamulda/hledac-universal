"""
BGP + Passive DNS Adapter — Sprint F214R
======================================

Extends existing BGP infrastructure (bgp_lane.py) with:
  - RIPE Stat API: IP → ASN, prefix, holder, country, org
  - BGP.tools API: historical prefix announcements, sibling prefixes
  - HackerTarget API: free passive DNS lookup

Pattern: AcquisitionLane-style async class (matches ct_lane/bgp_lane).
Session: injected via setter (scheduler pattern), fail-soft throughout.

Bounds:
  MAX_IPS_PER_SPRINT    = 50      — max IPs to query per sprint
  MAX_PDNS_RECORDS      = 100     — max PDNS records per domain
  RATE_LIMIT_S          = 1.0     — seconds between requests
  TIMEOUT_PER_REQUEST   = 15.0    — seconds

Env: HLEDAC_ENABLE_BGP_PDNS=1 to enable (default: 0)

INVARIANTS:
  - All HTTP calls use aiohttp.ClientSession (injected via setter)
  - Fail-soft throughout: errors never crash the sprint
  - RFC1918/loopback IPs filtered before any lookup
  - Rate limiting enforced per request
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiohttp
else:
    import aiohttp

logger = logging.getLogger(__name__)

# Try to import CanonicalFinding (lazy to avoid boot-path overhead)
_CanonicalFinding = None
try:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding as _CF
    _CanonicalFinding = _CF
except ImportError:
    pass

# ── Environment gating ─────────────────────────────────────────────────────────
_ENABLED: bool = os.environ.get("HLEDAC_ENABLE_BGP_PDNS", "0").lower() in (
    "1", "true", "yes", "on"
)

# ── Capability flags (for capabilities.py) ─────────────────────────────────────
BGP_LOOKUP_AVAILABLE: bool = _ENABLED
PASSIVE_DNS_AVAILABLE: bool = _ENABLED

# ── Bounds ────────────────────────────────────────────────────────────────────────
MAX_IPS_PER_SPRINT: int = 50
MAX_PDNS_RECORDS: int = 100
RATE_LIMIT_S: float = 1.0
TIMEOUT_PER_REQUEST: float = 15.0

# API endpoints
_RIPE_PREFIX_URL = "https://stat.ripe.net/data/prefix-overview/data.json"
_RIPE_WHOIS_URL = "https://stat.ripe.net/data/whois/data.json"
_BGP_TOOLS_URL = "https://bgp.tools/api"
_HACKER_TARGET_DNS = "https://api.hackertarget.com/dnslookup"

# ── Private IP filter ─────────────────────────────────────────────────────────
_PRIVATE_IP_RE = re.compile(
    r'^(?:10\.|172\.(?:1[6-9]|2\d|3[01])\.|192\.168\.|127\.|::1|fe80)'
)


def _is_private_ip(ip: str) -> bool:
    """Check if IP is RFC1918/loopback/private."""
    return bool(_PRIVATE_IP_RE.match(ip))


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass
class BGPFinding:
    """BGP intelligence finding from RIPE/BGP.tools."""
    ip: str = ""
    asn: int = 0
    asn_name: str = ""
    country_code: str = ""
    prefix: str = ""
    holder: str = ""
    source: str = "ripe"
    ts: float = 0.0

    def to_canonical_finding(self, query: str = "") -> Any | None:
        """Convert to CanonicalFinding for DuckDB ingestion."""
        try:
            if _CanonicalFinding is None:
                return None
            content_hash = hashlib.sha256(
                f"{self.ip or ''}:{self.asn}:{self.source}".encode()
            ).hexdigest()[:16]
            finding_id = f"bgp_{self.asn}_{content_hash}"

            metadata = {
                "asn": str(self.asn),
                "asn_name": self.asn_name,
                "ip": self.ip,
                "prefix": self.prefix,
                "holder": self.holder,
                "country": self.country_code,
            }

            return _CanonicalFinding(
                finding_id=finding_id,
                query=(query or f"bgp:{self.ip}")[:128],
                source_type="bgp_enrichment",
                confidence=0.88,
                ts=self.ts or time.time(),
                provenance=(f"asn:{self.asn}", f"ip:{self.ip}", f"prefix:{self.prefix}"),
                payload_text=str(metadata),
                accepted=True,
                reason="bgp_passive_dns",
                entropy=0.0,
                normalized_hash=None,
                duplicate=False,
            )
        except Exception:
            return None


@dataclass
class PDNSRecord:
    """Passive DNS record."""
    domain: str = ""
    record_type: str = ""
    value: str = ""
    first_seen: float = 0.0
    last_seen: float = 0.0
    source: str = "hackertarget"
    ts: float = 0.0

    def to_canonical_finding(self, query: str = "") -> Any | None:
        """Convert to CanonicalFinding for DuckDB ingestion."""
        try:
            if _CanonicalFinding is None:
                return None
            content_hash = hashlib.sha256(
                f"{self.domain}:{self.record_type}:{self.value}".encode()
            ).hexdigest()[:16]
            finding_id = f"pdns_{self.domain}_{self.record_type}_{content_hash[:8]}"

            metadata = {
                "domain": self.domain,
                "record_type": self.record_type,
                "value": self.value,
                "first_seen": self.first_seen,
                "last_seen": self.last_seen,
            }

            return _CanonicalFinding(
                finding_id=finding_id,
                query=(query or f"pdns:{self.domain}")[:128],
                source_type="passive_dns",
                confidence=0.82,
                ts=self.ts or time.time(),
                provenance=(f"domain:{self.domain}", f"type:{self.record_type}"),
                payload_text=str(metadata),
                accepted=True,
                reason="passive_dns",
                entropy=0.0,
                normalized_hash=None,
                duplicate=False,
            )
        except Exception:
            return None


# ── BGP Adapter ───────────────────────────────────────────────────────────────


# Default headers for API requests
_DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Hledac-OSINT/1.0 (research tool)",
}


async def _rate_limited_request(
    session: aiohttp.ClientSession,
    url: str,
    last_request: float,
    timeout: float = TIMEOUT_PER_REQUEST,
    extra_headers: dict | None = None,
) -> dict[str, Any] | None:
    """Make rate-limited JSON request, return parsed data or None."""
    elapsed = time.monotonic() - last_request
    if elapsed < RATE_LIMIT_S:
        await asyncio.sleep(RATE_LIMIT_S - elapsed)

    headers = dict(_DEFAULT_HEADERS)
    if extra_headers:
        headers.update(extra_headers)

    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers=headers,
        ) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception:
        pass
    return None


async def _rate_limited_text(
    session: aiohttp.ClientSession,
    url: str,
    last_request: float,
    timeout: float = TIMEOUT_PER_REQUEST,
) -> str | None:
    """Make rate-limited text request, return text or None."""
    elapsed = time.monotonic() - last_request
    if elapsed < RATE_LIMIT_S:
        await asyncio.sleep(RATE_LIMIT_S - elapsed)

    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers=_DEFAULT_HEADERS,
        ) as resp:
            if resp.status == 200:
                return await resp.text()
    except Exception:
        pass
    return None


async def ripestat_lookup_ip(
    ip: str,
    session: aiohttp.ClientSession,
    rate_limit_ref: float | None = None,
) -> BGPFinding | None:
    """
    RIPE Stat API: IP → ASN, prefix, holder, country, org.
    Returns BGPFinding or None on failure.
    """
    if not _ENABLED or _is_private_ip(ip):
        return None

    last_req = rate_limit_ref or 0.0

    # RIPE prefix-overview
    url = f"{_RIPE_PREFIX_URL}?resource={ip}"
    data = await _rate_limited_request(session, url, last_req)
    if not data:
        return None

    prefixes = data.get("data", {}).get("prefixes", [])
    if not prefixes:
        return None

    entry = prefixes[0]
    asn = entry.get("asn", 0)
    prefix = entry.get("prefix", "")
    holder = entry.get("holder", "")

    if not asn:
        return None

    # RIPE whois for country/org
    country = ""
    org_name = ""
    whois_url = f"{_RIPE_WHOIS_URL}?resource={asn}"
    whois_data = await _rate_limited_request(session, whois_url, last_req)
    if whois_data:
        objects = whois_data.get("data", {}).get("objects", {})
        for obj in objects.get("object", [])[:1]:
            for attr in obj.get("attributes", {}).get("attribute", []):
                name = attr.get("name", "")
                value = attr.get("value", "")
                if name == "country":
                    country = value
                elif name == "org-name":
                    org_name = value

    return BGPFinding(
        ip=ip,
        asn=asn,
        asn_name=org_name or holder,
        country_code=country,
        prefix=prefix,
        holder=holder,
        source="ripe",
        ts=time.time(),
    )


async def bgptools_prefix_history(
    prefix: str,
    session: aiohttp.ClientSession,
    rate_limit_ref: float = 0.0,
) -> list[BGPFinding]:
    """
    BGP.tools API: historical prefix announcements.
    Returns list of BGPFindings.
    """
    if not _ENABLED:
        return []

    url = f"{_BGP_TOOLS_URL}/prefix/{prefix}"
    data = await _rate_limited_request(session, url, rate_limit_ref)
    if not data:
        return []

    findings = []
    for entry in data.get("asns", [])[:20]:
        asn = entry.get("asn", 0)
        if asn:
            findings.append(BGPFinding(
                ip="",
                asn=asn,
                asn_name=entry.get("name", ""),
                country_code=entry.get("country", "") or "",
                prefix=prefix,
                holder="",
                source="bgp_tools",
                ts=time.time(),
            ))
    return findings


async def bgptools_sibling_prefixes(
    asn: str,
    session: aiohttp.ClientSession,
    rate_limit_ref: float = 0.0,
) -> list[str]:
    """
    BGP.tools API: all prefixes announced by this ASN.
    Returns list of prefix strings.
    """
    if not _ENABLED:
        return []

    asn_clean = asn.replace("AS", "").strip()
    url = f"{_BGP_TOOLS_URL}/asn/{asn_clean}"
    data = await _rate_limited_request(session, url, rate_limit_ref)
    if not data:
        return []

    prefixes = []
    for entry in data.get("prefixes", [])[:MAX_PDNS_RECORDS]:
        p = entry.get("prefix", "")
        if p:
            prefixes.append(p)
    return prefixes


# ── PassiveDNS Adapter ───────────────────────────────────────────────────────


async def hackertarget_pdns(
    domain: str,
    session: aiohttp.ClientSession,
    rate_limit_ref: float = 0.0,
) -> list[PDNSRecord]:
    """
    HackerTarget API: passive DNS lookup for domain.
    Returns list of PDNSRecord.
    """
    if not _ENABLED:
        return []

    url = f"{_HACKER_TARGET_DNS}?q={domain}"
    try:
        elapsed = time.monotonic() - rate_limit_ref
        if elapsed < RATE_LIMIT_S:
            await asyncio.sleep(RATE_LIMIT_S - elapsed)

        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_PER_REQUEST),
        ) as resp:
            if resp.status != 200:
                return []
            if resp.headers.get("Content-Type", "").startswith("application/json"):
                return []  # Error response

            text = await resp.text()
            if "error" in text.lower() or "quota" in text.lower():
                return []  # Error/quota response

            if not text or text.startswith("#"):
                return []

            records = []
            for line in text.splitlines()[:MAX_PDNS_RECORDS]:
                # HackerTarget format: "A : 1.2.3.4" or "A|1.2.3.4"
                parts = re.split(r"\s*:\s*|\|", line.strip(), maxsplit=1)
                if len(parts) < 2:
                    continue
                rec_type = parts[0].strip()
                value = parts[1].strip()
                if rec_type in ("A", "AAAA", "MX", "NS", "TXT", "CNAME", "PTR"):
                    records.append(PDNSRecord(
                        domain=domain,
                        record_type=rec_type,
                        value=value,
                        source="hackertarget",
                        ts=time.time(),
                    ))
            return records
    except Exception:
        return []


async def hackertarget_reverse_dns(
    ip: str,
    session: aiohttp.ClientSession,
    rate_limit_ref: float = 0.0,
) -> list[str]:
    """
    HackerTarget API: reverse DNS lookup for IP (find related domains).
    Returns list of domain strings.
    """
    if not _ENABLED or _is_private_ip(ip):
        return []

    url = f"{_HACKER_TARGET_DNS}?q={ip}"
    try:
        elapsed = time.monotonic() - rate_limit_ref
        if elapsed < RATE_LIMIT_S:
            await asyncio.sleep(RATE_LIMIT_S - elapsed)

        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_PER_REQUEST),
        ) as resp:
            if resp.status != 200:
                return []
            text = await resp.text()

            domains = []
            for line in text.splitlines()[:50]:
                parts = re.split(r"\s*:\s*|\|", line.strip(), maxsplit=1)
                if len(parts) >= 2 and parts[0] in ("PTR", "A", "AAAA"):
                    val = parts[1].strip()
                    if "." in val and not _is_private_ip(val):
                        domains.append(val)
            return domains[:50]
    except Exception:
        return []


# ── Compound Adapter Classes (for scheduler integration) ─────────────────────


class BGPAdapter:
    """
    BGP enrichment adapter for scheduler integration.

    Usage:
        adapter = BGPAdapter()
        adapter.set_session(aiohttp_session)
        finding = await adapter.lookup_asn("8.8.8.8")
    """
    __slots__ = ("_session", "_stats", "_semaphore", "_last_request")

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._stats: dict[str, int] = {
            "ips_processed": 0,
            "asns_resolved": 0,
            "prefixes_collected": 0,
            "errors": 0,
        }
        self._semaphore = asyncio.Semaphore(3)
        self._last_request: float = 0.0

    def set_session(self, session: aiohttp.ClientSession) -> None:
        """Inject aiohttp session."""
        self._session = session

    async def lookup_asn(self, ip: str) -> BGPFinding | None:
        """RIPE Stat API: IP → ASN, prefix, holder, country, org."""
        if not _ENABLED or not self._session or _is_private_ip(ip):
            return None

        if self._stats["ips_processed"] >= MAX_IPS_PER_SPRINT:
            return None

        async with self._semaphore:
            self._stats["ips_processed"] += 1
            result = await ripestat_lookup_ip(ip, self._session, self._last_request)
            self._last_request = time.monotonic()
            if result and result.asn:
                self._stats["asns_resolved"] += 1
            return result

    async def get_prefix_history(self, prefix: str) -> list[BGPFinding]:
        """BGP.tools API: historical prefix announcements."""
        if not _ENABLED or not self._session:
            return []
        async with self._semaphore:
            return await bgptools_prefix_history(prefix, self._session, self._last_request)

    async def find_sibling_prefixes(self, asn: str) -> list[str]:
        """BGP.tools API: all prefixes announced by this ASN."""
        if not _ENABLED or not self._session:
            return []
        async with self._semaphore:
            result = await bgptools_sibling_prefixes(asn, self._session, self._last_request)
            self._stats["prefixes_collected"] += len(result)
            return result

    def get_stats(self) -> dict[str, int]:
        """Return adapter statistics."""
        return self._stats.copy()


class PassiveDNSAdapter:
    """
    Passive DNS enrichment adapter for scheduler integration.

    Usage:
        adapter = PassiveDNSAdapter()
        adapter.set_session(aiohttp_session)
        records = await adapter.query_pdns("example.com")
    """
    __slots__ = ("_session", "_stats", "_semaphore", "_last_request")

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._stats: dict[str, int] = {
            "domains_processed": 0,
            "records_collected": 0,
            "errors": 0,
        }
        self._semaphore = asyncio.Semaphore(2)
        self._last_request: float = 0.0

    def set_session(self, session: aiohttp.ClientSession) -> None:
        """Inject aiohttp session."""
        self._session = session

    async def query_pdns(self, domain: str) -> list[PDNSRecord]:
        """HackerTarget API: passive DNS lookup for domain."""
        if not _ENABLED or not self._session:
            return []
        async with self._semaphore:
            self._stats["domains_processed"] += 1
            result = await hackertarget_pdns(domain, self._session, self._last_request)
            self._last_request = time.monotonic()
            self._stats["records_collected"] += len(result)
            return result

    async def find_related_domains(self, ip: str) -> list[str]:
        """HackerTarget API: reverse DNS lookup for IP."""
        if not _ENABLED or not self._session or _is_private_ip(ip):
            return []
        async with self._semaphore:
            return await hackertarget_reverse_dns(ip, self._session, self._last_request)

    def get_stats(self) -> dict[str, int]:
        """Return adapter statistics."""
        return self._stats.copy()


__all__ = [
    "BGP_LOOKUP_AVAILABLE",
    "PASSIVE_DNS_AVAILABLE",
    "BGPFinding",
    "PDNSRecord",
    "BGPAdapter",
    "PassiveDNSAdapter",
    "ripestat_lookup_ip",
    "bgptools_prefix_history",
    "bgptools_sibling_prefixes",
    "hackertarget_pdns",
    "hackertarget_reverse_dns",
]
